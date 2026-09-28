# PMMS MCP Client 集成指南

本文档面向**上层 MCP Client 开发者**，描述如何接入 PMMS（Primarius Modeling MCP Server）：

- 如何 spawn PMMS 进程并建立 stdio 连接
- MCP 协议握手与 capability 声明
- 真实 JSON-RPC 请求/响应示例
- Tool/Resource/Prompt 调用细节与返回结构
- 生命周期、超时、错误分类、并发限制

> 与 `MCP_API.md` 的关系：`MCP_API.md` 是给 MCP Agent（LLM）调用 Tool 时查参数用的 Resource（`pmms://api`），不包含启动/握手/协议细节；本文档是给人看的集成指南，不作为 Resource 暴露。

---

## 目录

- [1. PMMS 概览](#1-pmms-概览)
- [2. 部署模式与启动方式](#2-部署模式与启动方式)
- [3. MCP 协议概览](#3-mcp-协议概览)
- [4. 启动与握手](#4-启动与握手)
- [5. Tool 调用与返回结构](#5-tool-调用与返回结构)
- [6. Resource 读取](#6-resource-读取)
- [7. Prompt 获取](#7-prompt-获取)
- [8. 生命周期与超时](#8-生命周期与超时)
- [9. 错误分类与处理](#9-错误分类与处理)
- [10. 并发与独占限制](#10-并发与独占限制)
- [11. 完整 Client 调用示例](#11-完整-client-调用示例)
- [12. 集成检查清单](#12-集成检查清单)

---

## 1. PMMS 概览

PMMS 是半导体器件建模 MCP Server，把 MeQLab 建模系统的 gRPC 接口包装为 MCP 协议暴露给上层 Agent。

```
你的 Client (Agent) ──stdio JSON-RPC──> PMMS ──gRPC──> MeQLab (server 机独占实例)
```

PMMS 进程负责：
1. 通过 SSH 或本地 subprocess 启动独占的 MeQLab 实例
2. 维护与 MeQLab 的 gRPC 连接 + 心跳（保活 server 端 watchdog）
3. 把 MCP `tools/call` 等请求转发到 MeQLab，把返回结果格式化为 MCP 响应

**PMMS 暴露 3 类 MCP 对象**：
- **40 个 Tools**（建模全流程接口）
- **1 个 Resource**（`pmms://api`，Tool 接口详细说明，给 Agent 按需读取）
- **4 个 Prompts**（建模工作流模板，封装典型 Tool 调用顺序）

完整接口参数语义与返回示例见 `MCP_API.md`（运行时可通过 `resources/read` 读取 URI `pmms://api`）。本文档只讲集成相关内容。

---

## 2. 部署模式与启动方式

### 2.1 两种部署模式

| 模式 | CLI 参数 | MeQLab 启动方式 | gRPC 目标 | 适用场景 |
| --- | --- | --- | --- | --- |
| **SSH 模式**（默认） | `--ssh-host` / `--ssh-user` / `--ssh-key` 或 `--ssh-password` / `--meqlab-path` | SSH 远程后台启动 | `ssh_host:<动态端口>` | 远程 server 机部署 |
| **本地模式** | `--local --meqlab-path` | 本机 `subprocess.Popen` | `127.0.0.1:<动态端口>` | MeQLab 与 PMMS 同机（开发/测试） |

两种模式下 PMMS 的 MCP 协议行为**完全一致**，差异仅在底层 gRPC 连接目标。

### 2.2 启动命令

#### 2.2.1 用配置文件启动

```bash
pmms.exe --config config.txt
```

`config.txt` 格式：
```ini
ssh_host=10.0.0.5
ssh_user=admin
ssh_key=~/.ssh/id_rsa
meqlabpath=C:/MeQLab/bin/meqlab.exe
feature=prim|rf_main|ppei
log_level=INFO
```

#### 2.2.2 用 CLI 参数启动（推荐 Client 集成用）

```bash
pmms.exe --ssh-host 10.0.0.5 --ssh-user admin --ssh-key ~/.ssh/id_rsa \
         --meqlab-path "C:/MeQLab/bin/meqlab.exe" \
         --feature "prim|rf_main|ppei"
```

本地模式：
```bash
pmms.exe --local --meqlab-path "C:/MeQLab/bin/meqlab.exe"
```

#### 2.2.3 CLI 参数全表

| 参数 | 对应配置项 | 必填 | 说明 |
| --- | --- | --- | --- |
| `-c` / `--config` | 配置文件路径 | 否 | 优先级低于其他 CLI 参数 |
| `--ssh-host` | `ssh_host` | SSH 模式必填 | server 机 IP |
| `--ssh-user` | `ssh_user` | SSH 模式必填 | SSH 用户名 |
| `--ssh-port` | `ssh_port` | 否 | 默认 22 |
| `--ssh-key` | `ssh_key` | 二选一 | SSH 私钥路径 |
| `--ssh-password` | `ssh_password` | 二选一 | SSH 密码（不推荐明文传入） |
| `--meqlab-path` | `meqlabpath` | 是 | server 机或本机的 meqlab.exe 路径 |
| `--feature` | `feature` | 否 | 默认 `prim\|rf_main\|ppei` |
| `--prjdir` | `prjdir` | 否 | MeQLab 自动创建的工程路径（`--prjdir`），留空不传 |
| `--port-start` | `port_range_start` | 否 | 默认 2008 |
| `--port-end` | `port_range_end` | 否 | 默认 2100 |
| `--local` | `local` | 否 | 置 true 切换本地模式，跳过 SSH |

> `--ssh-key` 与 `--ssh-password` 都为空时使用 SSH agent / 默认密钥。

### 2.3 Client 如何 spawn PMMS

PMMS 通过 **stdio** 与 Client 通信，所以 Client 用 `subprocess.Popen` 启动 PMMS，把子进程的 `stdin` / `stdout` 接到 MCP SDK 的 transport 即可：

```python
import subprocess

proc = subprocess.Popen(
    ["pmms.exe",
     "--ssh-host", "10.0.0.5",
     "--ssh-user", "admin",
     "--ssh-key", "~/.ssh/id_rsa",
     "--meqlab-path", "C:/MeQLab/bin/meqlab.exe"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,  # PMMS 日志走 stderr，不要混入 stdout
    bufsize=0,
)

# 把 proc.stdout / proc.stdin 喂给你的 MCP Client SDK
```

**重要**：
- **`stderr` 不要并到 `stdout`**。PMMS 用 `stderr` 写日志（启动进度、心跳信息、错误堆栈），如果并到 stdout 会破坏 JSON-RPC 帧。
- PMMS 已在 `__main__.py` 中强制 `sys.stderr.reconfigure(encoding='utf-8')`，日志含中文也不会乱码。
- 建议另起一个线程异步读 `stderr` 用于排查问题，或写日志文件。

---

## 3. MCP 协议概览

### 3.1 依赖与版本

| 项 | 值 |
| --- | --- |
| MCP SDK 依赖 | `mcp>=1.26.0`（基于 [Model Context Protocol](https://modelcontextprotocol.io) 规范） |
| 传输方式 | **stdio**（JSON-RPC 2.0 over stdin/stdout，按行分隔的 JSON 消息） |
| 协议版本 | 由 SDK 在 `initialize` 握手中协商（通常 `2024-11-05` 或更新） |
| Server 名称 | `primarius-modeling` |
| Server 版本 | 与 `mcp` 包版本一致（当前 1.26.0） |

### 3.2 声明的 Capabilities

PMMS 在 `initialize` 响应中声明以下 capability：

```json
{
  "capabilities": {
    "tools": {},
    "resources": {},
    "prompts": {}
  }
}
```

- `tools`：支持 `tools/list`、`tools/call`
- `resources`：支持 `resources/list`、`resources/read`
- `prompts`：支持 `prompts/list`、`prompts/get`

无 `logging`、`roots`、`sampling`、`completion` 等 capability。

### 3.3 不支持的操作

- 不支持 `notifications/cancelled`（Tool 调用不可中途取消，需等返回或超时）
- 不支持 `resources/subscribe`（Resource 是静态的，按需 `resources/read` 即可）
- 不支持 `prompts/list` 的动态变化（Prompts 集合在 server 启动后固定）

---

## 4. 启动与握手

### 4.1 PMMS 进程启动时序

PMMS 进程启动后，在开始响应 MCP 请求前会执行一段初始化（约 5-30 秒，受网络/MeQLab 启动速度影响）：

```
1. 加载配置文件 + 应用 CLI 覆盖       (瞬时)
2. SSH 登录 server 机                  (1-5s)
3. 扫端口占用，找空闲端口 2008-2100    (1-3s)
4. SSH 后台启动 meqlab.exe             (1-3s)
5. 等待 gRPC 端口可 TCP 连接           (5-60s，超时 60s)
6. 建立 gRPC channel
7. 启动心跳线程（每 10s 一次 IsServiceAvailable）
8. 启动 MCP stdio_server，开始响应请求
```

**Client 应等待 `initialize` 请求成功响应后再认为 PMMS ready**。在步骤 1-7 期间，PMMS 还没启动 stdio_server，`initialize` 请求会阻塞在 stdin pipe 中，PMMS 一旦 ready 立即响应。

### 4.2 `initialize` 握手

MCP 协议要求 Client 在调用任何其他方法前必须先发 `initialize`。

**请求**：
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2024-11-05",
    "capabilities": {
      "roots": { "listChanged": false }
    },
    "clientInfo": {
      "name": "my-mcp-client",
      "version": "1.0.0"
    }
  }
}
```

**响应**（PMMS 实际返回结构）：
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": "2024-11-05",
    "capabilities": {
      "tools": {},
      "resources": {},
      "prompts": {}
    },
    "serverInfo": {
      "name": "primarius-modeling",
      "version": "1.26.0"
    },
    "instructions": null
  }
}
```

握手后**必须发 `notifications/initialized`** 通知 PMMS 握手完成：

```json
{
  "jsonrpc": "2.0",
  "method": "notifications/initialized"
}
```

> 这是 MCP 协议要求，不发可能导致部分 SDK 拒绝后续请求。

### 4.3 启动失败的处理

PMMS 启动阶段若失败（SSH 失败、端口全占用、MeQLab 启动超时），会**直接退出进程**而不进入 stdio_server 循环。Client 现象：
- `initialize` 请求永远不响应（stdin pipe 一直可写但 stdout 无输出）
- 进程退出导致 stdin/stdout pipe 断开

**建议处理**：Client 给 `initialize` 设 **60-90 秒超时**，超时后检查子进程是否还活着（`proc.poll()`），若已退出则读 `stderr` 日志排查。

---

## 5. Tool 调用与返回结构

### 5.1 `tools/list` 响应结构

PMMS 返回 40 个 Tool 定义，每个 Tool 的 `inputSchema` 是标准 JSON Schema 对象。

**请求**：
```json
{ "jsonrpc": "2.0", "id": 2, "method": "tools/list" }
```

**响应（截取）**：
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "tools": [
      {
        "name": "load_data",
        "description": "加载数据（iv/cv/sparam 等）。path 可为 server 端绝对路径或 projectdir/user/ 下的相对路径。device_type/device_polarity 未传时由服务端推断",
        "inputSchema": {
          "type": "object",
          "properties": {
            "data_type": {"type": "integer", "description": "数据类型枚举：0=SWEEP, 1=SPEC, 2=WAT", "enum": [0, 1, 2]},
            "data_source_name": {"type": "string", "description": "数据源名称，默认为 data1、data2..."},
            "path": {"type": "string", "description": "数据文件路径..."},
            "device_type": {"type": "string", "description": "器件类型..."},
            "device_polarity": {"type": "string", "description": "器件极性..."}
          },
          "required": ["data_type", "path"]
        }
      }
      // ... 其余 39 个 Tool
    ]
  }
}
```

### 5.2 `tools/call` 请求与响应

**请求**：
```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    "name": "load_data",
    "arguments": {
      "data_type": 0,
      "data_source_name": "iv",
      "path": "data/iv.dat"
    }
  }
}
```

**响应（成功）**：
```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "数据加载成功\n名称：iv\nID: 1"
      }
    ],
    "isError": false
  }
}
```

> **`content` 一定是 list，元素全是 `TextContent`**（`{type: "text", text: "..."}`）。PMMS 不会返回 `ImageContent` / `AudioContent` / `EmbeddedResource`。

### 5.3 返回内容 = 文本摘要

`MCP_API.md` 各 Tool 写的"返回示例"（如 `工程初始化成功\n路径：...`）就是 `content[0].text` 字段的实际内容。PMMS 在 `server.py:format_result` 中把结构化结果渲染为人可读的纯文本。

**这意味着**：client 拿到的不是结构化 JSON，而是格式化文本。如果 Client/Agent 需要结构化字段（如 `model_suite_id`、`job_id`），有两个选择：
1. **解析文本**：从 `text` 中用正则提取（脆弱，不推荐）
2. **调 list 接口反查**：例如 `add_model_source` 后调 `list_model_sources` 拿模型源名；`load_model` 后调 `list_available_models` 拿 ModelSuite ID；`start_optimize` 后用 `text` 中明文打印的 `Job ID: <uuid>` 提取

### 5.4 失败的两种形态

PMMS 失败响应分两类，**两类都是 `isError: false`**（不是 MCP 协议层的错误），靠 `text` 内容区分：

**业务错误**（gRPC 返回 `status.code != 0`）：
```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "错误：Tool 执行失败，错误码：1，错误信息：无活动工程"
      }
    ],
    "isError": false
  }
}
```

**调用异常**（参数错、网络断、gRPC 异常等）：
```json
{
  "jsonrpc": "2.0",
  "id": 5,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "错误：未知的 Tool: foo_bar"
      }
    ],
    "isError": false
  }
}
```

区分方法：`text` 以 `错误：Tool 执行失败，错误码：` 开头是业务错误（可读错误码/错误信息），以 `错误：` 开头但不含"错误码"是调用异常。详见 [§9 错误分类](#9-错误分类与处理)。

### 5.5 二进制 Tool 的特殊处理

`upload_file` 的 `chunk_data` 字段：
- inputSchema 声明 `"format": "byte"`
- Client 应传 **base64 编码后的字符串**（不是原始 bytes）
- PMMS 会 base64 解码后再 client-stream 上传到 gRPC server

`download_file` 的返回：
- `content[0].text` 是 JSON 字符串，结构为：
  ```json
  {
    "status": { "code": 0, "message": "" },
    "data": "<base64 编码的完整文件内容>",
    "size": 12345,
    "chunk_count": 3
  }
  ```
- Client 需 `json.loads(text)` 后取 `data` 字段，再 base64 解码还原文件

---

## 6. Resource 读取

### 6.1 `resources/list`

**请求**：
```json
{ "jsonrpc": "2.0", "id": 6, "method": "resources/list" }
```

**响应**：
```json
{
  "jsonrpc": "2.0",
  "id": 6,
  "result": {
    "resources": [
      {
        "uri": "pmms://api",
        "name": "api",
        "title": "PMMS MCP API 接口说明",
        "description": "全部 Tool 接口的详细说明：参数表、返回示例、前置依赖、数据结构汇总、Tool 索引。调用 Tool 前如需了解完整参数语义或返回格式，请读取本 Resource。",
        "mimeType": "text/markdown"
      }
    ]
  }
}
```

### 6.2 `resources/read`

**请求**：
```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "method": "resources/read",
  "params": { "uri": "pmms://api" }
}
```

**响应**：
```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "result": {
    "contents": [
      {
        "uri": "pmms://api",
        "mimeType": "text/markdown",
        "text": "# PMMS MCP API 调用文档\n\n本文档面向**上层 MCP Agent**..."
      }
    ]
  }
}
```

`text` 即 `MCP_API.md` 的完整内容（约 27KB）。建议 Client 缓存该内容，避免每次 Tool 调用前都重新拉取。

**未知 URI 的响应**：
```json
{
  "jsonrpc": "2.0",
  "id": 8,
  "error": {
    "code": -32602,
    "message": "未知的 Resource URI：pmms://unknown"
  }
}
```

注意：未知 URI 是 JSON-RPC 层 error（不是 `isError: false` 的文本错误）。

---

## 7. Prompt 获取

### 7.1 `prompts/list`

**请求**：
```json
{ "jsonrpc": "2.0", "id": 9, "method": "prompts/list" }
```

**响应（截取）**：
```json
{
  "jsonrpc": "2.0",
  "id": 9,
  "result": {
    "prompts": [
      {
        "name": "project_initialization",
        "description": "从零开始的工程初始化流程...",
        "arguments": [
          {"name": "project_name", "description": "工程名...", "required": false},
          {"name": "model_path", "description": "model card 文件路径...", "required": true},
          {"name": "data_path", "description": "测量数据文件路径...", "required": true},
          {"name": "data_type", "description": "数据类型：0=SWEEP, 1=SPEC, 2=WAT", "required": true}
        ]
      }
      // ... 其余 3 个 Prompt
    ]
  }
}
```

### 7.2 `prompts/get`

**请求**：
```json
{
  "jsonrpc": "2.0",
  "id": 10,
  "method": "prompts/get",
  "params": {
    "name": "project_initialization",
    "arguments": {
      "model_path": "D:/model/demo.lib",
      "data_path": "data/iv.dat",
      "data_type": "0"
    }
  }
}
```

**响应**：
```json
{
  "jsonrpc": "2.0",
  "id": 10,
  "result": {
    "description": "建模工作流 Prompt：project_initialization",
    "messages": [
      {
        "role": "user",
        "content": {
          "type": "text",
          "text": "请按以下顺序执行工程初始化流程..."
        }
      }
    ]
  }
}
```

### 7.3 Prompt arguments 类型约定

**所有 arguments 值必须是 string**，包括看起来像数字的字段：

| 字段 | 正确 | 错误 |
| --- | --- | --- |
| `data_type` | `"0"` / `"1"` / `"2"` | `0` / `1` / `2` |
| `project_name` | `"BestfittingProject"` | ✓ |
| `model_path` | `"D:/model/demo.lib"` | ✓ |

如果传非 string，PMMS 的 `_render_template` 会用 `.format()` 渲染，对非字符串占位符可能抛 `TypeError`，返回 JSON-RPC error。

### 7.4 必填参数缺失

PMMS 会校验 `arguments` 中 `required: true` 的字段：

```json
{
  "jsonrpc": "2.0",
  "id": 11,
  "error": {
    "code": -32603,
    "message": "Prompt project_initialization 缺少必填参数：model_path, data_type"
  }
}
```

### 7.5 4 个 Prompt 速览

| Prompt | 描述 | 必填参数 |
| --- | --- | --- |
| `project_initialization` | 工程初始化流程：generate_project → load_model → add_model_source → load_data → build_filter_by_data | `model_path`, `data_path`, `data_type` |
| `param_adjustment` | 参数手动逐参微调：clear_views → view → get_view_group_error → set_param 迭代 | `model_source_name`, `param_string` |
| `param_optimization` | 异步 Job 自动优化：clear_views → view（含 selection）→ start_optimize → 轮询 get_job_status | `model_source_name`, `param_string` |
| `qa_workflow` | QA 流程：start_qa → 轮询 get_job_status → dump_report | `report_path` |

详细工作流步骤与 12 个参考视图见 `MCP_API.md`。

---

## 8. 生命周期与超时

### 8.1 PMMS 生命周期

```
Client spawn PMMS
    ↓ (5-30s 启动时序，详见 §4.1)
PMMS ready, 响应 initialize
    ↓
Client 调用 tools/resources/prompts
    ↓
Client 关闭 stdio / 进程退出
    ↓
PMMS 检测到 stdin EOF, 退出 stdio_server 循环
    ↓
PMMS 停止心跳线程 + 关闭 gRPC + 关闭 SSH / kill 子进程
    ↓
SSH 模式：MeQLab 由 server 端 watchdog 在 2 分钟内自动退出
本地模式：MeQLab 子进程被 PMMS 直接 terminate/kill
    ↓
PMMS 进程退出
```

### 8.2 超时建议

| 操作 | 建议超时 | 说明 |
| --- | --- | --- |
| `initialize` | **90s** | 含 SSH 登录 + MeQLab 启动 + gRPC 端口就绪（最长 60s） |
| 普通 Tool 调用（load_data/view/set_param 等） | **30s** | gRPC 单次调用通常 < 5s，留 buffer |
| `optimize`（同步阻塞） | **30 分钟** | 长任务，建议优先用 `start_optimize` 异步 |
| `run_qa`（同步阻塞） | **30 分钟** | 长任务，建议优先用 `start_qa` 异步 |
| `upload_file` 单块 | **30s** | 1MB base64 约 1.3MB payload |
| `download_file` | **5 分钟** | 取决于文件大小，server-streaming 聚合 |
| `start_optimize` / `start_qa` | **5s** | 立即返回 job_id |
| `get_job_status` / `cancel_job` | **5s** | 不持锁，快 |
| `resources/read` | **5s** | `MCP_API.md` 约 27KB，读一次 |
| `prompts/get` | **5s** | 模板渲染，瞬时 |

### 8.3 心跳机制（对 Client 透明）

PMMS 内部维护一个心跳线程，每 10s 调一次 MeQLab 的 `IsServiceAvailable` 刷新 server 端 watchdog。**该机制对 Client 完全透明，无需 Client 调用 `is_service_available`**（该接口未暴露为 MCP Tool）。

如果 Client 长时间不调用任何 Tool（> 2 分钟），PMMS 心跳仍在跑，MeQLab 不会退出。但如果 Client 进程崩溃，PMMS 进程会因 stdin EOF 退出，心跳停止，MeQLab 在 2 分钟后自动退出。

### 8.4 长任务必须用异步

`optimize` 和 `run_qa` 是**同步阻塞**调用，会一直占用 gRPC 通道直到完成。长任务（> 1 分钟）必须改用异步：

| 同步（阻塞） | 异步（推荐） | 流程 |
| --- | --- | --- |
| `optimize` | `start_optimize` → `get_job_status` 轮询 | 立即返回 job_id，后台执行 |
| `run_qa` | `start_qa` → `get_job_status` 轮询 → `dump_report` | 立即返回 job_id，后台执行 |

异步模式轮询建议：**1-3 秒间隔**，`job_status != 0 (RUNNING)` 时停止。JobStatus 枚举：0=RUNNING, 1=DONE, 2=FAILED, 3=CANCELLED。

---

## 9. 错误分类与处理

### 9.1 三类错误

| 类型 | 现象 | 触发原因 | 处理建议 |
| --- | --- | --- | --- |
| **JSON-RPC 协议错误** | 响应有 `error` 字段（无 `result`），`code` 为 -32xxx | 未知方法/参数类型错/未知 URI/必填参数缺失/未知 Tool 名 | 检查请求格式 |
| **业务错误** | `result.content[0].text` 以 `错误：Tool 执行失败，错误码：` 开头 | gRPC 返回 `status.code != 0`（NO_PROJECT/MODEL_NOT_LOADED/BUSY 等） | 解析错误码，按错误码语义处理 |
| **调用异常** | `result.content[0].text` 以 `错误：` 开头但不含"错误码" | 参数缺失、gRPC 通道断、PMMS 内部异常 | 检查参数，查 PMMS stderr 日志 |

### 9.2 业务错误码

详见 `MCP_API.md` 错误码章节，关键几个：

| 错误码 | 名称 | Client 处理 |
| --- | --- | --- |
| 1 | `NO_PROJECT` | 先调 `generate_project` 或 `open_project` |
| 4 | `MODEL_NOT_LOADED` | 先调 `load_model` 再调 `add_model_source` |
| 9 | `BUSY` | 等待 0.5-2s 重试，指数退避到 10s，最多 30 次 |
| 12 | `JOB_NOT_FOUND` | `job_id` 错了或 Job 已被清理 |
| 13 | `CANCELLED` | Job 已取消 |

### 9.3 BUSY 重试策略

PMMS 当前**未实现 BUSY 自动重试**，收到 BUSY 直接返回给 Client。如需重试，Client 自行实现：

```python
import asyncio

async def call_tool_with_busy_retry(client, name, arguments, max_retries=30):
    delay = 0.5
    for i in range(max_retries):
        result = await client.call_tool(name, arguments)
        text = result.content[0].text
        if not text.startswith("错误：Tool 执行失败，错误码：9"):
            return result
        await asyncio.sleep(delay)
        delay = min(delay * 2, 10.0)  # 指数退避，上限 10s
    raise TimeoutError(f"连续 {max_retries} 次 BUSY，放弃")
```

### 9.4 PMMS 进程异常退出

如果 PMMS 进程崩溃（gRPC 异常未捕获、OOM 等），Client 现象：
- stdin/stdout pipe 断开
- `call_tool` 等请求超时或立即返回 pipe broken 错误

**建议**：Client 监听 `proc.poll()`，进程退出时立即关闭 MCP 会话，不要继续发请求。MeQLab 由 server 端 watchdog 在 2 分钟内自动清理，无需 Client 主动 kill 远端进程。

---

## 10. 并发与独占限制

### 10.1 单 Client 独占

**一个 PMMS 进程只服务一个 MCP Client**。原因：
- PMMS 内部维护单一 gRPC 连接 + 单一 MeQLab 实例
- workspace 状态（工程、模型、参数）是单 session 的，多 Client 共用会互相覆盖
- MeQLab server 端用 `ReentrantLock` 保护 workspace 单例，同一时刻只能一个修改操作

**不要**用同一个 PMMS 进程的 stdio 给多个 Client 接入。

### 10.2 多 Client 部署

每个 Client 启动**自己的 PMMS 进程**：

```
Client A → spawn PMMS-A → SSH 启动 MeQLab-A (端口 2008)
Client B → spawn PMMS-B → SSH 启动 MeQLab-B (端口 2009)
Client C → spawn PMMS-C → SSH 启动 MeQLab-C (端口 2010)
```

PMMS 之间靠端口动态分配（2008-2100 范围内扫第一个空闲端口）互不冲突。隔离靠：
1. 端口动态分配：其他 Client 不知道你的端口
2. SSH 凭证：无 SSH 凭证无法启动新 MeQLab 实例

### 10.3 Tool 调用并发

PMMS 处理 MCP 请求是**串行**的（stdio JSON-RPC 按 id 顺序处理）。Client 发多个并发 `tools/call` 时，PMMS 会按收到顺序逐个处理，不会并行执行 gRPC 调用。

如果需要"后台跑 QA + 同时调参"这种场景，应该用异步 Job（`start_qa` / `start_optimize`）：
- Job 启动后立即返回 job_id，不阻塞后续 Tool 调用
- Job 后台执行期间，Client 可以继续调其他 Tool
- 通过 `get_job_status` 轮询 Job 进度

### 10.4 长任务串行化的副作用

注意：异步 Job 在 server 端**也是串行**的（ReentrantLock 保护）。如果同时启动 `start_qa` 和 `start_optimize`：
- 第二个 Job 的后台 worker 会 `tryLock` 失败
- Job 状态会卡在 `JOB_RUNNING` 等第一个 Job 释放锁
- 不会立即报错，但完成时间会延后

**建议**：同一时刻只启动一个异步 Job，等 `job_status != RUNNING` 再启动下一个。

---

## 11. 完整 Client 调用示例

### 11.1 用官方 MCP Python SDK

```python
import asyncio
import subprocess
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters, stdio_client

async def main():
    # 1. spawn PMMS
    server_params = StdioServerParameters(
        command="pmms.exe",
        args=[
            "--ssh-host", "10.0.0.5",
            "--ssh-user", "admin",
            "--ssh-key", "~/.ssh/id_rsa",
            "--meqlab-path", "C:/MeQLab/bin/meqlab.exe",
        ],
        env=None,
    )

    async with stdio_client(server_params) as (read, write):
        # 2. 建立 Client 并握手（SDK 自动发 initialize）
        client = Client(name="my-mcp-client", version="1.0.0")
        async with client.connect(read, write) as session:
            # 3. 列出 Tools
            tools = await session.list_tools()
            print(f"PMMS 暴露 {len(tools.tools)} 个 Tool")

            # 4. 调用 Tool
            result = await session.call_tool("load_data", {
                "data_type": 0,
                "data_source_name": "iv",
                "path": "data/iv.dat",
            })
            print(result.content[0].text)
            # 输出: "数据加载成功\n名称：iv\nID: 1"

            # 5. 读取 Resource
            res = await session.read_resource("pmms://api")
            api_doc = res.contents[0].text
            print(f"API 文档 {len(api_doc)} 字符")

            # 6. 获取 Prompt
            prompt = await session.get_prompt(
                "project_initialization",
                arguments={
                    "model_path": "D:/model/demo.lib",
                    "data_path": "data/iv.dat",
                    "data_type": "0",
                },
            )
            workflow_text = prompt.messages[0].content.text
            print(workflow_text[:200])

            # 7. 异步 Job 示例
            start_result = await session.call_tool("start_qa", {})
            # text: "任务已启动\nJob ID: <uuid>\n..."
            import re
            job_id = re.search(r"Job ID: (\S+)", start_result.content[0].text).group(1)

            # 8. 轮询
            while True:
                await asyncio.sleep(2)
                status_result = await session.call_tool("get_job_status", {"job_id": job_id})
                text = status_result.content[0].text
                print(text)
                if "Job 状态: RUNNING" not in text:
                    break

asyncio.run(main())
```

### 11.2 用裸 JSON-RPC（无 SDK）

```python
import subprocess
import json
import uuid

proc = subprocess.Popen(
    ["pmms.exe", "--ssh-host", "10.0.0.5", "--ssh-user", "admin",
     "--ssh-key", "~/.ssh/id_rsa", "--meqlab-path", "C:/MeQLab/bin/meqlab.exe"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True, bufsize=1,
)

next_id = 1

def send(method, params=None, notification=False):
    global next_id
    msg = {"jsonrpc": "2.0", "method": method}
    if not notification:
        msg["id"] = next_id
        next_id += 1
    if params is not None:
        msg["params"] = params
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()
    if notification:
        return None
    line = proc.stdout.readline()
    return json.loads(line)

# 1. 握手
resp = send("initialize", {
    "protocolVersion": "2024-11-05",
    "capabilities": {},
    "clientInfo": {"name": "raw-client", "version": "1.0.0"},
})
print(resp["result"]["serverInfo"])  # {"name": "primarius-modeling", "version": "1.26.0"}

# 2. 通知握手完成
send("notifications/initialized", notification=True)

# 3. 调 Tool
resp = send("tools/call", {
    "name": "load_data",
    "arguments": {"data_type": 0, "path": "data/iv.dat"},
})
print(resp["result"]["content"][0].text)

proc.terminate()
```

---

## 12. 集成检查清单

接入 PMMS 前请逐项确认：

### 部署检查
- [ ] 已确认部署模式（SSH / 本地），如 SSH 模式已准备好 ssh_host / ssh_user / ssh_key
- [ ] 已确认 server 机上 `meqlab.exe` 路径正确
- [ ] 已确认端口范围 2008-2100 在 server 机上未被全部占用
- [ ] 已测试 `pmms.exe --config config.txt` 能手动启动成功（看 stderr 日志出现 `✓ MCP Server 启动成功`）

### 进程与 stdio
- [ ] Client 用 `subprocess.Popen` 启动 PMMS，stdin/stdout 用 PIPE
- [ ] `stderr` 单独接管，不并到 stdout（避免污染 JSON-RPC 帧）
- [ ] 设了 `initialize` 超时（建议 90s），超时后检查 `proc.poll()`
- [ ] 监听 `proc.poll()`，PMMS 退出时立即关闭会话

### 协议握手
- [ ] `initialize` 请求带了 `protocolVersion` / `capabilities` / `clientInfo`
- [ ] 收到 `initialize` 响应后发了 `notifications/initialized` 通知
- [ ] 检查响应中 `serverInfo.name == "primarius-modeling"`

### Tool 调用
- [ ] 知道 `tools/call` 返回的 `content` 是 list of `TextContent`，业务数据在 `text` 字段
- [ ] 知道业务数据是**格式化文本**不是结构化 JSON，需要时通过 list 接口反查 ID
- [ ] `upload_file` 的 `chunk_data` 传 base64 字符串
- [ ] `download_file` 的返回要先 `json.loads(text)` 再 base64 解码 `data` 字段
- [ ] `optimize` / `run_qa` 是同步阻塞，长任务用 `start_optimize` / `start_qa` 异步
- [ ] 异步 Job 轮询间隔 1-3 秒，`job_status != 0` 停止

### 错误处理
- [ ] 区分 JSON-RPC error（`error` 字段）vs Tool 失败（`result.content[0].text` 含"错误："）
- [ ] 业务错误从 `text` 解析错误码（`错误：Tool 执行失败，错误码：<n>，错误信息：<msg>`）
- [ ] 对 `BUSY`（错误码 9）实现了指数退避重试
- [ ] 对 `NO_PROJECT`（1）/ `MODEL_NOT_LOADED`（4）自动补前置步骤

### Resource / Prompt
- [ ] 缓存了 `pmms://api` 内容，不每次 Tool 调用前都重新拉
- [ ] `prompts/get` 的 `arguments` 值全部转 string（含 `data_type` 数字字段）
- [ ] `prompts/get` 必填参数缺失会返回 JSON-RPC error，做好兜底

### 并发
- [ ] 同一 PMMS 进程不接多个 Client
- [ ] 多 Client 场景每个 Client 启动自己的 PMMS 进程
- [ ] 同一时刻只启动一个异步 Job，等 `job_status != RUNNING` 再启下一个

### 生命周期
- [ ] Client 退出时优雅关闭 stdio（让 PMMS 收到 stdin EOF 自动退出）
- [ ] 知道 PMMS 退出后 MeQLab 由 server 端 watchdog 2 分钟内自动清理（SSH 模式）/ 被 PMMS 直接 kill（本地模式）
- [ ] 不需要 Client 主动 SSH kill 远端 MeQLab 进程

---

## 附录：相关文档

| 文档 | 受众 | 说明 |
| --- | --- | --- |
| `MCP_API.md` | MCP Agent（LLM） | 40 个 Tool / 1 个 Resource / 4 个 Prompt 的参数语义与返回示例（作为 `pmms://api` Resource 暴露） |
| **`MCP_INTEGRATION.md`**（本文档） | Client 开发者 | 启动、握手、JSON-RPC 示例、生命周期、错误分类、并发限制 |
