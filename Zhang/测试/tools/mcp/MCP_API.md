# PMMS MCP API 调用文档

本文档面向**上层 MCP Agent**，描述 PMMS（Primarius Modeling MCP Server）通过 MCP 协议暴露的全部 **Tools / Resources / Prompts** 的调用方式、参数语义、返回结构与典型用法。

> 与 `README.md` 的关系：`README.md` 偏部署/协议层（SSH、gRPC、心跳、配置项）；本文档聚焦 MCP 层（Agent 通过 `tools/call`、`resources/read`、`prompts/get` 调用 PMMS 时需要知道的内容）。两者参数语义一致，本文档以 `tools.py` 的实际 `inputSchema` 为准。

---

## 目录

- [调用约定](#调用约定)
- [Tools 总览（按分组）](#tools-总览按分组)
- [1. 文件操作](#1-文件操作)
  - [1.1 upload_file](#11-upload_file)
  - [1.2 download_file](#12-download_file)
- [2. 工程操作](#2-工程操作)
  - [2.1 generate_project](#21-generate_project)
  - [2.2 open_project](#22-open_project)
  - [2.3 save_current_project](#23-save_current_project)
  - [2.4 close_current_project](#24-close_current_project)
  - [2.5 exist_project](#25-exist_project)
- [3. 工艺信息操作](#3-工艺信息操作)
  - [3.1 set_variable](#31-set_variable)
  - [3.2 list_variable](#32-list_variable)
  - [3.3 get_variable](#33-get_variable)
- [4. 数据 I/O](#4-数据-io)
  - [4.1 load_data](#41-load_data)
  - [4.2 extract_spec](#42-extract_spec)
  - [4.3 deem](#43-deem)
  - [4.4 list_data_sources](#44-list_data_sources)
  - [4.5 get_data_detail](#45-get_data_detail)
  - [4.6 remove_data_source](#46-remove_data_source)
- [5. 模型选型](#5-模型选型)
  - [5.1 load_model](#51-load_model)
  - [5.2 list_available_models](#52-list_available_models)
  - [5.3 add_model_source](#53-add_model_source)
  - [5.4 remove_model_source](#54-remove_model_source)
  - [5.5 list_model_sources](#55-list_model_sources)
  - [5.6 add_devicecopy](#56-add_devicecopy)
  - [5.7 clear_devicecopy](#57-clear_devicecopy)
  - [5.8 save_model](#58-save_model)
- [6. 模型参数操作](#6-模型参数操作)
  - [6.1 get_param](#61-get_param)
  - [6.2 set_param](#62-set_param)
  - [6.3 remove_param](#63-remove_param)
  - [6.4 list_params](#64-list_params)
- [7. Filter 操作](#7-filter-操作)
  - [7.1 list_filters](#71-list_filters)
  - [7.2 build_filter_by_data](#72-build_filter_by_data)
  - [7.3 build_filter_by_template](#73-build_filter_by_template)
  - [7.4 remove_filter](#74-remove_filter)
- [8. 视图操作](#8-视图操作)
  - [8.1 view](#81-view)
  - [8.2 dump_view_group](#82-dump_view_group)
  - [8.3 clear_views](#83-clear_views)
  - [8.4 get_view_group_error](#84-get_view_group_error)
- [9. 优化操作](#9-优化操作)
  - [9.1 optimize](#91-optimize)
- [10. 模型仿真](#10-模型仿真)
  - [10.1 save_sim_result](#101-save_sim_result)
- [11. 模型 QA 操作](#11-模型-qa-操作)
  - [11.1 run_qa](#111-run_qa)
  - [11.2 dump_report](#112-dump_report)
- [12. Job 异步任务](#12-job-异步任务)
  - [12.1 start_optimize](#121-start_optimize)
  - [12.2 start_qa](#122-start_qa)
  - [12.3 get_job_status](#123-get_job_status)
  - [12.4 cancel_job](#124-cancel_job)
- [Resources](#resources)
- [Prompts](#prompts)
- [通用数据结构](#通用数据结构)
- [错误码](#错误码)
- [并发模型与 BUSY 处理](#并发模型与-busy-处理)
- [Tool 索引](#tool-索引)

---

## 调用约定

### 协议与传输

- PMMS 作为 MCP Server 通过 **stdio + JSON-RPC** 与上层 Agent 通信
- Tool 调用走标准 `tools/call`，Resource 读取走 `resources/read`，Prompt 获取走 `prompts/get`
- 所有 Tool 调用结果以 `list[TextContent]` 形式返回，文本内容为：
  - 成功：人可读的格式化摘要（见各 Tool 的"返回示例"）
  - 失败：`错误：Tool 执行失败，错误码：<code>，错误信息：<message>` 或 `错误：<异常描述>`

### 参数命名

- MCP Tool 参数一律使用 **snake_case**（与 `tools.py` 中 `inputSchema` 一致），与 README 中部分 proto 风格的 camelCase（如 `viewFields`）不同。本调用文档以 snake_case 为准。
- 不存在可选默认值的字段，未传入即由 server 端取默认值，**不会**因为未传而报错。

### 路径约定

涉及 `path` 参数的 Tool（`upload_file` 除外，因其写入位置固定）支持两类路径：

| 路径类型 | 示例 | 说明 |
| --- | --- | --- |
| server 端绝对路径 | `D:/model/demo.lib` | 直接落在 server 机文件系统 |
| projectdir 相对路径 | `data/iv.dat` | 解析为 `projectdir/user/<相对路径>` |

`upload_file` 的写入位置固定为 `projectdir/user/<group_name>/`（`group_name` 未传时为 `projectdir/user/`），不接受自定义 path。

### 二进制传输

- **`upload_file`** 的 `chunk_data` 在 MCP 层为 base64 字符串（`format: byte`）。PMMS 收到后会 base64 解码为 bytes，多块缓冲后在 `is_last=true` 时一次性以 client-streaming 上传到 gRPC server。
- **`download_file`** 返回时，PMMS 已聚合 server-streaming 的所有 chunk 为完整 bytes，再 base64 编码后封装为 JSON 字符串返回：
  ```json
  {
    "status": { "code": 0, "message": "" },
    "data": "<base64-encoded file content>",
    "size": 12345,
    "chunk_count": 3
  }
  ```
  Agent 取 `data` 字段做 base64 解码即可还原完整文件。

### 调用前置依赖链

```
generate_project / open_project
        │
        ├─ load_data ──── build_filter_by_data ──┐
        │                                        │
        └─ load_model ─── add_model_source ──────┼── view ── get_view_group_error
                            │                    │           ├── optimize / start_optimize
                            └── add_devicecopy   │           ├── save_sim_result
                                                 │           └── run_qa / start_qa ── dump_report
                                                 └─ build_filter_by_template
```

任一前置步骤未完成，对应 Tool 会返回 `code != 0` 的错误（多为 `NO_PROJECT=1` / `MODEL_NOT_LOADED=4`）。

---

## Tools 总览（按分组）

| 分组 | Tool 数 | Tool 名 |
| --- | --- | --- |
| 文件操作 | 2 | `upload_file`、`download_file` |
| 工程操作 | 5 | `generate_project`、`open_project`、`save_current_project`、`close_current_project`、`exist_project` |
| 工艺信息 | 3 | `set_variable`、`list_variable`、`get_variable` |
| 数据 I/O | 6 | `load_data`、`extract_spec`、`deem`、`list_data_sources`、`get_data_detail`、`remove_data_source` |
| 模型选型 | 8 | `load_model`、`list_available_models`、`add_model_source`、`remove_model_source`、`list_model_sources`、`add_devicecopy`、`clear_devicecopy`、`save_model` |
| 模型参数 | 4 | `get_param`、`set_param`、`remove_param`、`list_params` |
| Filter | 4 | `list_filters`、`build_filter_by_data`、`build_filter_by_template`、`remove_filter` |
| 视图 | 4 | `view`、`dump_view_group`、`clear_views`、`get_view_group_error` |
| 优化 | 1 | `optimize`（同步） |
| 模型仿真 | 1 | `save_sim_result` |
| 模型 QA | 2 | `run_qa`（同步）、`dump_report` |
| Job 异步 | 4 | `start_optimize`、`start_qa`、`get_job_status`、`cancel_job` |

共计 **44 个 Tool**。

---

## 1. 文件操作

### 1.1 upload_file

**接口描述**：文件上传。采用分块协议：客户端将文件按 1MB 切块，多次调用本 Tool 传 `chunk_data`，最后一块置 `is_last=true`。服务端按 `file_name` 累积拼接，存放到 `projectdir/user/<group_name>/`，`group_name` 未传时存 `projectdir/user/`。

| 参数 | 类型 | 必填 | 描述 |
| --- | --- | --- | --- |
| `file_name` | string | 是 | 文件名称 |
| `group_name` | string | 否 | 文件父文件夹名称 |
| `chunk_data` | string (byte, base64) | 是 | 分块数据，每块不超过 1MB |
| `is_last` | boolean | 是 | 是否为最后一块 |

**返回**：
- 非最后块：`已接收分块（N 块），等待后续分块`
- 最后块：`文件上传成功`

**调用示例**（伪代码）：
```python
# 将本地文件 base64 切块上传
with open("iv.dat", "rb") as f:
    data = f.read()

chunks = [data[i:i+1024*1024] for i in range(0, len(data), 1024*1024)]
for i, chunk in enumerate(chunks):
    await client.call_tool("upload_file", {
        "file_name": "iv.dat",
        "group_name": "data",
        "chunk_data": base64.b64encode(chunk).decode(),
        "is_last": i == len(chunks) - 1,
    })
```

### 1.2 download_file

**接口描述**：文件下载。`path` 可为文件或文件夹路径（传入文件夹时，服务端打包后流式返回）。PMMS 已聚合所有分块为完整 bytes 并 base64 编码后封装在 JSON 中返回。

| 参数 | 类型 | 必填 | 描述 |
| --- | --- | --- | --- |
| `path` | string | 是 | 文件或文件夹路径，可为绝对路径或 `projectdir` 相对路径 |

**返回**：JSON 字符串
```json
{
  "status": { "code": 0, "message": "" },
  "data": "<base64-encoded>",
  "size": 12345,
  "chunk_count": 3
}
```

**调用示例**：
```python
result = await client.call_tool("download_file", {"path": "data/iv.dat"})
payload = json.loads(result.text)
with open("iv.dat", "wb") as f:
    f.write(base64.b64decode(payload["data"]))
```

---

## 2. 工程操作

### 2.1 generate_project

**接口描述**：初始化建模工程。`project_name` 未传时由服务端分配。

| 参数 | 类型 | 必填 | 描述 | 示例 |
| --- | --- | --- | --- | --- |
| `project_name` | string | 否 | 工程名 | `BestfittingProject` |
| `device_type` | string | 否 | 器件类型，默认 `MOSFET` | `Mosfet` |
| `device_polarity` | string | 否 | 器件极性 `NMOS` 或 `PMOS`，默认 `NMOS` | `NMOS` |

**返回示例**：
```
工程初始化成功
路径：D:/projectdir/user/BestfittingProject
器件类型：Mosfet
器件极性：NMOS
```

### 2.2 open_project

**接口描述**：打开建模工程。

| 参数 | 类型 | 必填 | 描述 | 示例 |
| --- | --- | --- | --- | --- |
| `project_name` | string | 是 | 工程名 | `BestfittingProject` |

**返回示例**：
```
工程打开成功
路径：D:/projectdir/user/BestfittingProject
```

### 2.3 save_current_project

**接口描述**：保存当前建模工程（工程、模型、参数、视图配置）。无参数。

**返回**：`工程保存成功`

### 2.4 close_current_project

**接口描述**：关闭当前建模工程，会自动保存。无参数。

**返回**：`工程关闭成功`

### 2.5 exist_project

**接口描述**：判断工程是否存在。

| 参数 | 类型 | 必填 | 描述 |
| --- | --- | --- | --- |
| `project_name` | string | 是 | 工程名 |

**返回示例**：`工程存在：是` 或 `工程存在：否`

---

## 3. 工艺信息操作

### 3.1 set_variable

**接口描述**：设置工艺信息。

| 参数 | 类型 | 必填 | 描述 | 示例 |
| --- | --- | --- | --- | --- |
| `name` | string | 是 | 工艺名称 | `vdd` |
| `value` | string | 是 | 工艺值 | `3.3` |

**返回**：`工艺信息设置成功`

### 3.2 list_variable

**接口描述**：展示所有工艺信息。无参数。

**返回示例**：
```
工艺信息列表:
  - vdd: 3.3 V
  - temp: 25
```

### 3.3 get_variable

**接口描述**：获取工艺信息。

| 参数 | 类型 | 必填 | 描述 | 示例 |
| --- | --- | --- | --- | --- |
| `name` | string | 是 | 工艺名称 | `vdd` |

**返回示例**：`3.3`

---

## 4. 数据 I/O

### 4.1 load_data

**接口描述**：加载数据（iv/cv/sparam 等）。`path` 可为 server 端绝对路径或 `projectdir/user/` 下相对路径。`device_type`/`device_polarity` 未传时由服务端根据数据文件自动推断。

| 参数 | 类型 | 必填 | 描述 | 示例 |
| --- | --- | --- | --- | --- |
| `data_type` | integer (enum: 0/1/2) | 是 | 0=SWEEP, 1=SPEC, 2=WAT | `0` |
| `path` | string | 是 | 数据文件路径 | `data/iv.dat` |
| `data_source_name` | string | 否 | 数据源名称，默认 `data1`/`data2`... | `iv` |
| `device_type` | string | 否 | 器件类型，默认 `MOSFET` | `Mosfet` |
| `device_polarity` | string | 否 | 器件极性，默认 `NMOS` | `NMOS` |

**返回示例**：
```
数据加载成功
名称：iv
ID: 1
```

### 4.2 extract_spec

**接口描述**：从 sweep 数据抽取 spec。

| 参数 | 类型 | 必填 | 描述 | 示例 |
| --- | --- | --- | --- | --- |
| `data_source_name` | string | 是 | 数据源名称 | `iv` |

**返回**：`Spec 抽取成功`

### 4.3 deem

**接口描述**：S 参数去嵌。

| 参数 | 类型 | 必填 | 描述 | 示例 |
| --- | --- | --- | --- | --- |
| `dut_source_name` | string | 是 | DUT 数据源名称 | `sparam` |
| `open_source_name` | string | 是 | Open 数据源名称 | `open` |
| `short_source_name` | string | 是 | Short 数据源名称 | `short` |

**返回示例**：
```
S 参数去嵌成功
名称：sparam_deem
```

### 4.4 list_data_sources

**接口描述**：列出已加载的所有数据源。无参数。

**返回示例**：
```
数据源列表:
  - iv (ID: 1)
  - cv (ID: 2)
```

### 4.5 get_data_detail

**接口描述**：获取数据源详细信息（如 IV 曲线的具体数据点）。

| 参数 | 类型 | 必填 | 描述 | 示例 |
| --- | --- | --- | --- | --- |
| `data_source_name` | string | 是 | 数据源名称 | `iv` |

**返回示例**：
```
数据详情:
名称：iv
类型：Mosfet
页数：12
```

### 4.6 remove_data_source

**接口描述**：删除数据源。

| 参数 | 类型 | 必填 | 描述 | 示例 |
| --- | --- | --- | --- | --- |
| `data_source_name` | string | 是 | 数据源名称 | `iv` |

**返回**：`数据源删除成功`

---

## 5. 模型选型

### 5.1 load_model

**接口描述**：加载 model card。

| 参数 | 类型 | 必填 | 描述 | 示例 |
| --- | --- | --- | --- | --- |
| `path` | string | 是 | 模型文件路径 | `D:/model/demo.lib` |
| `format` | string | 否 | `hspice`/`spectre`，默认 `hspice` | `hspice` |

**返回示例**：
```
模型加载成功
路径：D:/model/demo.lib
格式：hspice
```

> 返回的 `ModelSuite.id` 是后续 `add_model_source` / `save_model` 必需的 `model_suite_id`，需从 Resource `pmms://api` 或服务端日志中读取结构化字段，或调用 `list_available_models` 查询。

### 5.2 list_available_models

**接口描述**：展示当前已加载的模型。无参数。

**返回示例**：
```
可用模型列表:
  - ID: 0, 路径：D:/model/demo.lib
  - ID: 1, 路径：D:/model/demo2.lib
```

### 5.3 add_model_source

**接口描述**：添加模型源，配置模型仿真所需条件。

**前置依赖**：必须先执行 `load_model`。

| 参数 | 类型 | 必填 | 描述 | 示例 |
| --- | --- | --- | --- | --- |
| `model_suite_id` | integer | 是 | `load_model` 返回的 ModelSuite.id | `0` |
| `lib_name` | string | 否 | 选择 lib，默认为第一个 lib | `tt` |
| `model_name` | string | 否 | 选择 model，默认第一个 model/subckt | `nch_ckt` |
| `simulator` | string | 否 | `Nano`/`Hspice`/`Spectre`，默认 `Nano` | `Nano` |

**返回示例**：
```
模型源添加成功（共 3 个）:
  - 名称：nch_ckt_tt_N，模拟器：Nano
  - 名称：nch_ckt_ff_N，模拟器：Nano
  - 名称：nch_ckt_ss_N，模拟器：Nano
```

### 5.4 remove_model_source

**接口描述**：删除模型源。

| 参数 | 类型 | 必填 | 描述 | 示例 |
| --- | --- | --- | --- | --- |
| `model_source_name` | string | 是 | 模型源名称 | `nch_ckt_tt_N` |

**返回**：`模型源删除成功`

### 5.5 list_model_sources

**接口描述**：列出已添加的模型源。

**前置依赖**：必须先执行 `load_model`。

| 参数 | 类型 | 必填 | 描述 | 示例 |
| --- | --- | --- | --- | --- |
| `device_type` | string | 否 | 器件类型，不填则列出所有 | `Mosfet` |

**返回示例**：
```
模型源列表（共 3 个）:
  - 名称：nch_ckt_tt_N，模拟器：Nano
  - 名称：nch_ckt_ff_N，模拟器：Nano
```

### 5.6 add_devicecopy

**接口描述**：建立模型本征参数与物理尺寸的关联。

**前置依赖**：必须先执行 `add_model_source`。

| 参数 | 类型 | 必填 | 描述 | 示例 |
| --- | --- | --- | --- | --- |
| `model_source_name` | string | 是 | 模型源名称 | `nch_ckt_tt_N` |
| `params` | string | 是 | 关联参数，多个以逗号分隔 | `cgdo,cgso` |
| `device_string` | string | 是 | 关联器件尺寸 | `w=1e-6,l=1e-6` |

**返回**：`器件尺寸关联建立成功`

### 5.7 clear_devicecopy

**接口描述**：删除模型本征参数与物理尺寸的关联。无参数。

**返回**：`器件尺寸关联已清除`

### 5.8 save_model

**接口描述**：保存 model card。

| 参数 | 类型 | 必填 | 描述 | 示例 |
| --- | --- | --- | --- | --- |
| `model_suite_id` | integer | 是 | `load_model` 返回的 ModelSuite.id | `0` |
| `path` | string | 是 | 模型文件保存路径 | `D:/model/demo.lib` |

**返回**：`模型保存成功`

---

## 6. 模型参数操作

### 6.1 get_param

**接口描述**：获取模型参数值。

| 参数 | 类型 | 必填 | 描述 | 示例 |
| --- | --- | --- | --- | --- |
| `model_source_name` | string | 是 | 模型源名称 | `nch_ckt_tt_N` |
| `param_name` | string | 是 | 参数名称 | `vch` |
| `device` | string | 否 | 器件尺寸信息，仅用于已添加 devicecopy 的模型 | `w=1e-6` |

**返回示例**：
```
参数名：vth0
值：0.5
```

### 6.2 set_param

**接口描述**：设置或添加模型参数值。`param_value`/`param_min`/`param_max`/`param_step` 均为可选，未传入的字段保持原值不变。

> **proto3 限制说明**：`param_min`/`param_max`/`param_step` 为 `double` 标量字段，proto3 无法区分"未传入"与"显式传 0.0"。PMMS 当前默认值为 `0.0`，意味着 client 不传等价于传 `0.0`。若 server 端直接落库，原值会被覆盖为 `0.0`。建议 server 端基于 `param_value` 是否非空判断本次更新范围，或在协议升级时改用 `optional double` / wrapper message。

| 参数 | 类型 | 必填 | 描述 | 示例 |
| --- | --- | --- | --- | --- |
| `model_source_name` | string | 是 | 模型源名称 | `nch_ckt_tt_N` |
| `param_name` | string | 是 | 参数名称 | `vth0` |
| `const_index` | integer | 否 | 公式中的常数项位置，0 为第一个常数项，默认 0 | `0` |
| `node_name` | string | 否 | 在指定模型节点查找或添加参数，不指定自动查找 | `nch_ckt` |
| `param_value` | string | 否 | 参数值，可为公式或值，默认 NaN | `"1.0"` |
| `param_min` | number | 否 | 参数下边界，默认 NaN | `0` |
| `param_max` | number | 否 | 参数上边界，默认 NaN | `10` |
| `param_step` | number | 否 | 参数步长，默认 NaN | `1.0` |

**返回**：`参数设置成功`

### 6.3 remove_param

**接口描述**：删除模型参数。

| 参数 | 类型 | 必填 | 描述 | 示例 |
| --- | --- | --- | --- | --- |
| `model_source_name` | string | 是 | 模型源名称 | `nch_ckt_tt_N` |
| `param_name` | string | 是 | 参数名称 | `vch` |
| `node_name` | string | 否 | 在指定模型节点查找参数，不指定自动查找 | `nch_ckt` |

**返回**：`参数删除成功`

### 6.4 list_params

**接口描述**：展示参数列表。

| 参数 | 类型 | 必填 | 描述 | 示例 |
| --- | --- | --- | --- | --- |
| `model_source_name` | string | 是 | 模型源名称 | `nch_ckt_tt_N` |

**返回示例**：
```
参数列表:
  - vth0: 0.5
  - u0: 0.03
  - voff: 0.1
```

---

## 7. Filter 操作

### 7.1 list_filters

**接口描述**：展示当前所有 filter。无参数。

**返回示例**：
```
Filter 列表 (共 2 个):
  - iv (iv)
  - cv (cv)
```

### 7.2 build_filter_by_data

**接口描述**：根据选择数据构建 filter。

| 参数 | 类型 | 必填 | 描述 | 示例 |
| --- | --- | --- | --- | --- |
| `data_source_name` | string | 是 | 数据源名称 | `iv` |

**返回**：`Filter 构建成功`

### 7.3 build_filter_by_template

**接口描述**：根据 filter 模板文件构建 filter。

| 参数 | 类型 | 必填 | 描述 | 示例 |
| --- | --- | --- | --- | --- |
| `filter_template_path` | string | 是 | filter 模板文件路径 | `filter/template.ini` |

> 模板(.ini)编写语法见 Resource `pmms://template_writing_guide`（重点 §5 Filter 模板、§12.1 完整范例）。典型流程：编写模板内容 → [`upload_file`](#11-upload_file) 上传到 `projectdir/user/` → 本接口构建 → `list_filters` 验证；也可直接使用 Prompt `filter_template_build`（见 [Prompts](#prompts)）一键执行该流程。

**返回**：`Filter 构建成功`

### 7.4 remove_filter

**接口描述**：删除 filter。

| 参数 | 类型 | 必填 | 描述 | 示例 |
| --- | --- | --- | --- | --- |
| `filter_name` | string | 是 | 指定 filter 名称 | `iv` |

**返回**：`Filter 删除成功`

---

## 8. 视图操作

### 8.1 view

**接口描述**：选择视图。`view_fields` 为数组，每个元素描述一个视图的选择条件。

**前置依赖**：相关的 filter、数据源/模型源应已通过对应接口准备就绪。

| 参数 | 类型 | 必填 | 描述 |
| --- | --- | --- | --- |
| `view_fields` | array of object | 是 | 视图选择字段列表 |

**`view_fields[i]` 子字段**：

| 字段 | 类型 | 描述 | 示例 |
| --- | --- | --- | --- |
| `filter_string` | string | 选择 filter（「关键字,值」；name 必填，type 可省且仅合法短名） | `name,idsat` |
| `source_string` | string | 选择数据/模型源 | `iv, nch_ckt_tt_N` |
| `device_string` | string | 选择器件信息，仅用于多尺寸视图 | `W=max` |
| `page_string` | string | 选择 sweep curve，仅用于 sweep 图 | `Id_Vgs_Vbs@vds=vdthx` |
| `spec_string` | string | 选择 spec，仅用于 spec trend 图 | `idlin,idsat` |
| `selection_string` | string | 选择优化目标区域。`x(a,b)`=x 轴绝对范围（用户明确指定 x 范围时优先）；`xr(a,b)`=相对百分比范围，`xr(0,1)`=整张图、`xr(0,0.5)`=前 50%。**使用 optimize/start_optimize 时必填** | `x(0,1.2)`、`xr(0,0.5)` |
| `prop_string` | string | 配置视图属性（如 `logy` 对数坐标） | `logy` |

> 各字段的完整语法、"想要什么图 → 填什么字段"的图型对照表与调用示例，见 Resource `pmms://view_fields_guide`。

**返回**：`视图选择成功`

**调用示例**：
```python
await client.call_tool("view", {
    "view_fields": [
        {
            "filter_string": "type,op,name,idsat",
            "source_string": "iv, nch_ckt_tt_N",
            "page_string": "id_vgs",
            "selection_string": "xr(0,0.5)",
            "prop_string": "logy"
        },
        {
            "filter_string": "type,op,name,cv",
            "source_string": "cv, nch_ckt_tt_N",
            "page_string": "cgs_vds",
            "selection_string": "xr(0,1)"
        }
    ]
})
```

### 8.2 dump_view_group

**接口描述**：导出当前视图组，默认为 xlsx 格式。

| 参数 | 类型 | 必填 | 描述 | 示例 |
| --- | --- | --- | --- | --- |
| `path` | string | 是 | report 文件保存路径 | `D:/report/report.xlsx` |

**返回**：`视图组导出成功`

### 8.3 clear_views

**接口描述**：清空当前视图。无参数。

**返回**：`视图已清空`

### 8.4 get_view_group_error

**接口描述**：获取当前视图组所有视图的测量数据与模型仿真结果之间的误差。

**前置依赖**：必须先执行 `view`，且视图组中已存在数据源与模型源匹配的视图。无参数。

**返回示例**：
```
视图组误差（共 2 个视图）:
  - Id-Vgs: error=0.05
  - Id-Vds: error=0.08
聚合误差：0.065
```

---

## 9. 优化操作

### 9.1 optimize

**接口描述**：执行优化（**同步阻塞**，会一直占用 gRPC 通道直到完成）。

**前置依赖**：必须先执行 `view`，且 `view_fields` 的 `selection_string` 已指定优化目标区域。

> 长任务建议改用 [`start_optimize`](#121-start_optimize) 走 Job 异步模式，避免阻塞。

| 参数 | 类型 | 必填 | 描述 | 示例 |
| --- | --- | --- | --- | --- |
| `model_source_name` | string | 是 | 参与优化的参数所属模型源名（须为已加载模型源，PMMS 会前置校验）；优先复用 `view` 的 `source_string` 第二段，或 `list_model_sources` 查询 | `nch_ckt_tt_N` |
| `param_string` | string | 是 | 参数名列表，英文逗号分隔，**不含模型源名**（服务端组装为 `<模型源名>,<参数名>[...]`）；支持 `prefix.`/`end.` 与 `device(...)`（完整语法见 Resource `pmms://param_fields_guide`） | `vth0,u0` |

**返回示例**：
```
优化完成
误差：0.4
```

---

## 10. 模型仿真

### 10.1 save_sim_result

**接口描述**：保存模型仿真结果。服务端按 `data_source_name` 执行仿真，将数据保存到 `path`；`path` 未传时默认存到 `projectdir/user/simdata`。`model_source_name` 未传时默认使用 primary model，无则用第一个 modelsource。

| 参数 | 类型 | 必填 | 描述 | 示例 |
| --- | --- | --- | --- | --- |
| `data_source_name` | string | 是 | 数据源名称 | `iv` |
| `model_source_name` | string | 否 | 模型源名称 | `nch_ckt_tt_N` |
| `path` | string | 否 | 仿真数据文件保存路径 | `D:/sim/iv_sim.dat` |

**返回示例**：
```
仿真结果保存成功
路径：simdata/iv_sim.dat
```

---

## 11. 模型 QA 操作

### 11.1 run_qa

**接口描述**：执行 QA 检查（**同步阻塞**）。

> 长任务建议改用 [`start_qa`](#122-start_qa) 走 Job 异步模式。无参数。

**返回**：`QA 检查完成`

### 11.2 dump_report

**接口描述**：导出 QA 报告，默认 xlsx 格式。

| 参数 | 类型 | 必填 | 描述 | 示例 |
| --- | --- | --- | --- | --- |
| `path` | string | 是 | report 文件保存路径 | `D:/report/report.xlsx` |

**返回**：`QA 报告导出成功`

---

## 12. Job 异步任务

长任务（优化、QA）支持异步 Job 模式，避免阻塞 gRPC 通道：

| Tool | 输入 | 输出 | 备注 |
| --- | --- | --- | --- |
| `start_optimize` | `{ model_source_name, param_string }` | `{ job_id }` | 立即返回，后台执行；进度按 task 完成比例推进 |
| `start_qa` | `{}` | `{ job_id }` | 立即返回，后台执行；进度按 filter 完成比例推进 |
| `get_job_status` | `{ job_id }` | JobState 完整结构 | 不持锁，可随时调用 |
| `cancel_job` | `{ job_id }` | `{}` | 不持锁；当前 task/filter 完成后才生效 |

### 12.1 start_optimize

**接口描述**：启动异步优化任务，立即返回 `job_id`。

**前置依赖**：必须先执行 `view`，且 `view_fields` 的 `selection_string` 已指定优化目标区域。

| 参数 | 类型 | 必填 | 描述 | 示例 |
| --- | --- | --- | --- | --- |
| `model_source_name` | string | 是 | 参与优化的参数所属模型源名，同 `optimize`（PMMS 前置校验是否为已加载模型源） | `nch_ckt_tt_N` |
| `param_string` | string | 是 | 参数名列表，英文逗号分隔，**不含模型源名**，同 `optimize` | `vth0,u0` |

**返回示例**：
```
任务已启动
Job ID: 550e8400-e29b-41d4-a716-446655440000

请使用 get_job_status 工具查询进度，job_status 不为 0(RUNNING) 时停止轮询
```

### 12.2 start_qa

**接口描述**：启动异步 QA 任务，立即返回 `job_id`。无参数。

**返回示例**：
```
任务已启动
Job ID: 550e8400-e29b-41d4-a716-446655440001

请使用 get_job_status 工具查询进度，job_status 不为 0(RUNNING) 时停止轮询
```

### 12.3 get_job_status

**接口描述**：查询 Job 状态与进度。不持 session 锁，可随时调用。

| 参数 | 类型 | 必填 | 描述 | 示例 |
| --- | --- | --- | --- | --- |
| `job_id` | string | 是 | 任务 ID（由 `start_optimize` 或 `start_qa` 返回） | `550e8400-...` |

**JobStatus 枚举**：

| 值 | 名称 | 描述 |
| --- | --- | --- |
| 0 | `JOB_RUNNING` | 运行中 |
| 1 | `JOB_DONE` | 完成 |
| 2 | `JOB_FAILED` | 失败（看 `error_message`） |
| 3 | `JOB_CANCELLED` | 已取消 |

**返回示例**（进行中）：
```
Job 状态: RUNNING
进度: 45%
启动时间: 1787654321000
```

**返回示例**（已完成）：
```
Job 状态: DONE
进度: 100%
启动时间: 1787654321000
结束时间: 1787654381000
结果: {"qa":"ok"}
```

**轮询建议**：
1. 调 `start_qa` / `start_optimize` 拿 `job_id`
2. 每隔 1-3 秒调 `get_job_status`
3. `job_status != 0 (RUNNING)` 时停止轮询
4. `JOB_DONE` → 读 `result_json`
5. `JOB_FAILED` → 读 `error_message` 反馈给 Agent
6. `JOB_CANCELLED` → 视为已取消
7. 超时阈值建议 5-30 分钟

### 12.4 cancel_job

**接口描述**：取消 Job。当前正在执行的 task/filter 完成后才生效；已结束（DONE/FAILED/CANCELLED）的 Job 不可取消。

| 参数 | 类型 | 必填 | 描述 |
| --- | --- | --- | --- |
| `job_id` | string | 是 | 任务 ID |

**返回**：`任务已取消（或已结束）`

---

## Resources

PMMS 通过 MCP `resources/list` 暴露以下 Resource，供 Agent 按需读取（不在每次对话全量加载）：

| URI | 名称 | 描述 | MIME |
| --- | --- | --- | --- |
| `pmms://api` | PMMS MCP API 接口说明 | 全部 Tool 接口的详细说明：参数表、返回示例、前置依赖、数据结构汇总、Tool 索引 | `text/markdown` |
| `pmms://template_writing_guide` | MeQLab 模板(.ini)语法编写指导 | Filter、Report、ViewGroup、Part 模板及 device/page pattern 的全部写法；编写/修改模板或让 AI 生成模板前读取 | `text/markdown` |
| `pmms://view_fields_guide` | view 工具 view_fields 填写指南 | view_fields 各字段语法、"图型 → 字段组合"对照表与调用示例；调用 `view` 前读取 | `text/markdown` |
| `pmms://param_fields_guide` | optimize / start_optimize 参数填写指南（model_source_name + param_string） | `model_source_name`（必填）与 `param_string`（参数名列表，不含模型源名）填写方法、与模板 §13.2 的对应、参数名匹配、device(...) 用法、常见错误；调用 optimize/start_optimize 前读取 | `text/markdown` |

**读取示例**：
```python
# MCP resources/read
result = await client.read_resource("pmms://api")
print(result.contents[0].text)  # 即本文件内容
```

调用 Tool 前如需了解完整参数语义或返回格式，先读取 `pmms://api`。

---

## Prompts

PMMS 通过 MCP `prompts/list` 暴露以下 5 个建模工作流 Prompt，封装典型 Tool 调用顺序，供 Agent 一键触发：

| Prompt 名称 | 描述 | 参数 |
| --- | --- | --- |
| `project_initialization` | 从零开始的工程初始化流程：`generate_project` → `load_model` → `add_model_source` → `load_data` → `build_filter_by_data` | `project_name`(选填), `model_path`(必填), `data_path`(必填), `data_type`(必填, 0/1/2) |
| `param_adjustment` | 参数调整工作流（手动逐参微调）：迭代 `clear_views` → `view` → `get_view_group_error` → `set_param` 直到误差收敛 | `model_source_name`(必填), `param_string`(必填) |
| `param_optimization` | 参数优化工作流（异步 Job 自动优化）：迭代 `clear_views` → `view`(含 `selection_string`) → `start_optimize` → 轮询 `get_job_status` | `model_source_name`(必填), `param_string`(必填) |
| `qa_workflow` | QA 检查与报告导出流程：`start_qa` → 轮询 `get_job_status` → `dump_report` | `report_path`(必填) |
| `filter_template_build` | 自定义 filter 构建流程：编写模板(.ini)（语法见 Resource `pmms://template_writing_guide`）→ `upload_file` 上传 → `build_filter_by_template` → `list_filters` 验证 | `requirement`(必填), `template_name`(选填) |

**调用示例**：
```python
# MCP prompts/get
result = await client.get_prompt("project_initialization", {
    "model_path": "D:/model/demo.lib",
    "data_path": "data/iv.dat",
    "data_type": "0",
})
# result.messages[0].content.text 即为渲染后的"行动指南"，喂给 Agent 作为 user message
```

### 视图配置参考

`param_adjustment` 与 `param_optimization` Prompt 内置 12 个参考视图，Agent 在调用 `view` 时可参考：

**CV 类视图**（`filter_string` 用「关键字,值」、多组用英文逗号分隔，示例 `type,op,name,cv`；`source_string=cv`；`name` 必填、`type` 可省）：
1. Cgs-Vds：`page_string=cgs_vds`, `selection_string=xr(0,1)`
2. Cds-Vds：`page_string=cds_vds`, `selection_string=xr(0,1)`
3. Cgd-Vds：`page_string=cgd_vds`, `selection_string=xr(0,1)`
4. Cgd-Vds（对数）：加 `prop_string=logy`

**IV 类视图**（`filter_string` 用「关键字,值」、多组用英文逗号分隔，示例 `type,op,name,idsat`；`source_string=iv`；`name` 必填、`type` 可省）：
5. Id-Vgs：`page_string=id_vgs`, `selection_string=xr(0,1)`
6. Id-Vgs（对数）：加 `prop_string=logy`
7. Gm-Vgs：`page_string=(id_vgs,alg,dy/dx)`, `prop_string=logy`
8. Id-Vds：`page_string=id_vds`, `selection_string=xr(0,1)`
9. Gds-Vds：`page_string=(id_vds,alg,dx/dy)`
10. Gds-Vds（对数）：加 `prop_string=logy`
11. Igs-Vgs：`page_string=ig_vgs`
12. Igs-Vgs（对数）：加 `prop_string=logy`

> `filter_string` 格式为「关键字,值」成对、多组用英文逗号分隔；name 必填，type/group 非必填，type 仅支持合法短名（op/mp/mdvi/svi/histogram/st/scatter/mismatch），**不存在 iv/cv**。实际值需根据 `build_filter_by_data` 构建的 filter 填入。可先调用 `list_filters` 查看现有 filter。

---

## 通用数据结构

### Status

所有 response 第一个字段：
```json
{ "code": 0, "message": "" }
```
`code=0` 成功，非 0 失败，`message` 为人类可读说明。

### Project

```json
{
    "path": "",
    "device_type": "",
    "device_polarity": "",
    "model_suite_count": 0,
    "data_source_count": 0,
    "model_source_count": 0,
    "enhance_source_count": 0,
    "filter_count": 0,
    "variable_count": 0,
    "device_count": 0,
    "pagetype_count": 0,
    "spec_count": 0
}
```

### Variable

```json
{
    "name": "",
    "unit": "",
    "description": "",
    "value": ""
}
```

### DataSource

```json
{
    "name": "",
    "id": 0,
    "group": "",
    "device_type": "",
    "device_polarity": "",
    "page_count": 0,
    "specdata_count": 0
}
```

### ModelSuite

`path` 是 server 端路径；`libs` 的 key 为 PVT corner 名称（如 `tt`/`ff`/`ss`），value 为该 corner 下包含的 model/subckt 名称列表，以逗号分隔。

```json
{
    "id": 0,
    "format_type": "",
    "path": "",
    "libs": {
        "tt": "",
        "ff": "",
        "ss": ""
    }
}
```

### ModelSource

```json
{
    "name": "",
    "id": 0,
    "group": "",
    "device_type": "",
    "device_polarity": "",
    "lib_name": "",
    "model_name": "",
    "simulator": "",
    "subckt": true
}
```

### ModelParam

```json
{
    "name": "",
    "value": 0,
    "strvalue": ""
}
```

### Filter

```json
{
    "name": "",
    "group": "",
    "type": "",
    "sources": "",
    "devices": "",
    "pages": "",
    "specs": "",
    "rule": "",
    "prop": ""
}
```

### ViewError

```json
{
    "name": "",
    "error": 0
}
```

### JobState

```json
{
    "status": { "code": 0, "message": "" },
    "job_id": "550e8400-e29b-41d4-a716-446655440000",
    "job_status": 1,
    "progress": 100,
    "result_json": "{\"qa\":\"ok\"}",
    "error_message": "",
    "started_at": 1787654321000,
    "finished_at": 1787654381000
}
```

---

## 错误码

| 错误码 | 名称 | 描述 |
| --- | --- | --- |
| 0 | `OK` | 成功 |
| 1 | `NO_PROJECT` | 无活动工程（未打开或新建工程） |
| 2 | `PARAM_INVALID` | 参数无效 |
| 3 | `FILE_NOT_FOUND` | 文件不存在 |
| 4 | `MODEL_NOT_LOADED` | 模型未加载 |
| 5 | `DATA_FORMAT_ERROR` | 数据格式错误 |
| 6 | `OPT_FAILED` | 优化失败 |
| 7 | `SIM_FAILED` | 仿真失败 |
| 8 | `INTERNAL_ERROR` | 内部错误 |
| 9 | `BUSY` | 另一个操作进行中，请稍后重试 |
| 10 | `UNAUTHENTICATED` | 保留枚举值，v4 已移除 token 鉴权，当前不会返回 |
| 11 | `NOT_IMPLEMENTED` | 接口尚未实现（暂未启用的桩） |
| 12 | `JOB_NOT_FOUND` | Job ID 不存在 |
| 13 | `CANCELLED` | 任务已取消 |

PMMS 在 Tool 返回非 0 错误码时格式化为：
```
错误：Tool 执行失败，错误码：<code>，错误信息：<message>
```

---

## 并发模型与 BUSY 处理

server 当前为**单 session** 模式：内部 `ReentrantLock` 保护 workspace 单例，同一时刻只能有一个修改操作进行中。

- server 端并发修改操作立即返回 `code=BUSY`（9）
- Job 异步 RPC（`start_optimize` / `start_qa`）立即返回 `job_id`，不持锁；后台 worker 用 `tryLock` 抢锁
- `get_job_status` / `cancel_job` 不持锁，可随时调用

**BUSY 重试策略（client 端建议）**：
- 收到 `code=BUSY` → 等待 0.5-2s 后重试同一请求（指数退避到 ~10s）
- 连续 30 次 BUSY → 报错给 Agent，建议其先调 `is_service_available` 排查

> **当前实现状态**：PMMS 的 `server.py` 调用层**尚未实现 BUSY 自动重试**，收到 BUSY 即直接把错误码返回给 Agent。如需自动重试，由上层 Agent 自行实现。

---

## Tool 索引

按字母序排列，方便快速定位：

| Tool 名 | 分组 | 是否同步阻塞 | 前置依赖 |
| --- | --- | --- | --- |
| `add_devicecopy` | 模型选型 | 否 | `add_model_source` |
| `add_model_source` | 模型选型 | 否 | `load_model` |
| `build_filter_by_data` | Filter | 否 | `load_data` |
| `build_filter_by_template` | Filter | 否 | 无 |
| `cancel_job` | Job 异步 | 否 | `start_optimize` / `start_qa` |
| `clear_devicecopy` | 模型选型 | 否 | `add_model_source` |
| `clear_views` | 视图 | 否 | 无 |
| `close_current_project` | 工程 | 否 | 已有活动工程 |
| `deem` | 数据 I/O | 否 | `load_data`（DUT/open/short 三组） |
| `download_file` | 文件 | 否 | 无 |
| `dump_report` | 模型 QA | 否 | `run_qa` / `start_qa` 完成 |
| `dump_view_group` | 视图 | 否 | `view` |
| `exist_project` | 工程 | 否 | 无 |
| `extract_spec` | 数据 I/O | 否 | `load_data`（sweep） |
| `generate_project` | 工程 | 否 | 无 |
| `get_data_detail` | 数据 I/O | 否 | `load_data` |
| `get_job_status` | Job 异步 | 否 | `start_optimize` / `start_qa` |
| `get_param` | 模型参数 | 否 | `add_model_source` |
| `get_variable` | 工艺信息 | 否 | 已有活动工程 |
| `get_view_group_error` | 视图 | 否 | `view`（含数据+模型源） |
| `list_available_models` | 模型选型 | 否 | `load_model` |
| `list_data_sources` | 数据 I/O | 否 | 无 |
| `list_filters` | Filter | 否 | 无 |
| `list_model_sources` | 模型选型 | 否 | `load_model` |
| `list_params` | 模型参数 | 否 | `add_model_source` |
| `list_variable` | 工艺信息 | 否 | 已有活动工程 |
| `load_data` | 数据 I/O | 否 | 已有活动工程 |
| `load_model` | 模型选型 | 否 | 已有活动工程 |
| `open_project` | 工程 | 否 | 工程存在 |
| `optimize` | 优化 | **是** | `view`（含 `selection_string`） |
| `remove_data_source` | 数据 I/O | 否 | 已有活动工程 |
| `remove_filter` | Filter | 否 | 无 |
| `remove_model_source` | 模型选型 | 否 | `load_model` |
| `remove_param` | 模型参数 | 否 | `add_model_source` |
| `run_qa` | 模型 QA | **是** | 已有活动工程 + 模型源 |
| `save_current_project` | 工程 | 否 | 已有活动工程 |
| `save_model` | 模型选型 | 否 | `load_model` |
| `save_sim_result` | 模型仿真 | 否 | `load_data` + `add_model_source` |
| `set_param` | 模型参数 | 否 | `add_model_source` |
| `set_variable` | 工艺信息 | 否 | 已有活动工程 |
| `start_optimize` | Job 异步 | 否（立即返回） | `view`（含 `selection_string`） |
| `start_qa` | Job 异步 | 否（立即返回） | 已有活动工程 + 模型源 |
| `upload_file` | 文件 | 否 | 无 |
| `view` | 视图 | 否 | filter + 数据源 + 模型源已就绪 |
