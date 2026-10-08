# ASM-HEMT 101.6.0：Cadence Spectre + MeQLab + LangGraph 参数反向提取

本仓库保存一套可复现的单器件 ASM-HEMT 参数反向提取流程：

1. 在 IC Linux 上使用 Cadence Spectre 生成同一器件的仿真数据。
2. 将 Id-Vg、Id-Vd 曲线载入 MeQLab。
3. 通过 SSH wrapper 将 MeQLab 产生的候选网表发送到 IC Linux 的真实 Spectre。
4. LangGraph 中的 DeepSeek Agent 发起五参数优化，Kimi 对最终结果进行评审。

本项目不让大模型直接读取真值参数。Agent 可见的是曲线、误差、参数名和允许范围；参数搜索由 MeQLab 优化器执行。

## 当前实验状态

- 单器件 Spectre 真值仿真已成功。
- Id-Vg：71 点，VDS=5 V。
- Id-Vd：101 点，VGS=0 V。
- 初始综合误差：`0.553793116420489`。
- MeQLab 已实际生成多组候选参数，并成功调用远程 Spectre；候选仿真返回码为 0。
- 当前未得到最终拟合参数。最后一次运行被模型 API 网关连接故障中断，详见 `langgraph/RESUME_2026-10-09.md`。

## 目录

```text
ASMHEMT_LangGraph_Project/
├── README.md
├── .env.example
├── .gitignore
├── cadence_simulation/       # Spectre 网表、模型、日志和完整 raw 输出
├── data/                     # 便于单独查看的仿真归档及解析数据
├── environment/              # PMMS、主机拓扑及运行版本模板
└── langgraph/
    ├── eda_agent/            # LangGraph、双模型服务、PMMS 客户端
    ├── scripts/              # 仿真桥接、解析、探测和正式运行脚本
    ├── configs/              # MeQLab/ASM-HEMT 工作流配置
    ├── tests/                # 离线测试
    ├── artifacts/            # 配置引用的输入数据及历史运行状态
    ├── requirements.txt
    └── main.py
```

## 安全与许可证

- 本仓库不包含 API Key、SSH 私钥、账户密码、Cadence 安装文件或许可证文件。
- `.env`、私钥、日志中的令牌和本机 IDE 文件已被 `.gitignore` 排除。
- `cadence_simulation/asmhemt_nano_isothermal.va` 来自 ASM-HEMT 模型包。公开上传前，请自行确认模型许可证允许重新分发。
- 使用 Cadence Spectre 必须拥有有效的 Cadence 安装和许可证。

## 运行环境

已验证的版本：

- Windows 端 Python 3.10.21
- LangGraph 1.2.12
- MCP 2.2.0
- OpenAI Python SDK 3.15.0
- python-dotenv 1.2.2
- IC Linux Spectre：`/opt/cadence/SPECTRE231/bin/spectre`
- MeQLab Linux：通过 PMMS gRPC 控制

实际机器地址、用户名和路径应按自己的环境修改。示例拓扑见 `environment/TOPOLOGY.md`。

## 1. 安装 Python 环境

在 Windows PowerShell 中进入 `langgraph`：

```powershell
cd langgraph
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

复制环境模板：

```powershell
Copy-Item ..\.env.example .env
```

编辑 `.env`，填入自己的模型网关和 API Key。不要提交 `.env`。

## 2. 生成 Cadence Spectre 真值数据

将 `cadence_simulation` 复制到装有 Spectre 的 IC Linux，然后执行：

```bash
cd cadence_simulation
export CDS_LIC_FILE=/path/to/your/cadence/license.dat
export LM_LICENSE_FILE="$CDS_LIC_FILE"
bash run_truth.sh
```

脚本默认使用：

```text
/opt/cadence/SPECTRE231/bin/spectre
```

可通过 `SPECTRE_BIN` 覆盖：

```bash
SPECTRE_BIN=/custom/path/spectre bash run_truth.sh
```

主要输出：

- `truth_all.log`
- `truth_all.raw/dcVg.dc`
- `truth_all.raw/dcVd.dc`
- `truth_all.raw/acCV.ac`
- `truth_all.raw/tranPulse.tran.tran`
- `truth_all.raw/spS.sp`
- `truth_all.raw/noiseOne.noise`

仓库中已包含本次成功运行的输出，可直接用于复核。

## 3. 配置 MeQLab 到远程 Spectre

在 MeQLab Linux 上：

1. 建立到 IC Linux 的 SSH 公钥认证。
2. 将 `langgraph/scripts/meqlab_spectre_ssh_wrapper.sh` 安装为：

   ```bash
   mkdir -p "$HOME/bin"
   cp meqlab_spectre_ssh_wrapper.sh "$HOME/bin/spectre"
   chmod 755 "$HOME/bin/spectre"
   ```

3. 设置 wrapper 环境变量，例如：

   ```bash
   export MEQLAB_SPECTRE_HOST=IC_LINUX_IP
   export MEQLAB_SPECTRE_USER=IC_USER
   export MEQLAB_SPECTRE_KEY="$HOME/.ssh/meqlab_to_ic_ed25519"
   export MEQLAB_SPECTRE_BIN=/opt/cadence/SPECTRE231/bin/spectre
   export MEQLAB_SPECTRE_LICENSE=/path/to/license.dat
   export MEQLAB_ASMHEMT_DIR="$HOME/asmhemt101_6"
   ```

4. 将 MeQLab 的本地 Spectre 命令设为 `/home/<user>/bin/spectre`，并关闭 MeQLab 自带的 remote Spectre 模式。仓库中的 `meqlab_general.properties`、`meqlab_simulate.properties` 和 `meqlab_spectre.properties` 是本次验证使用的参考配置。

wrapper 会把 MeQLab 的临时网表和模型复制到 IC Linux，运行真实 Spectre，再把 `job.raw` 和 `job.log` 返回给 MeQLab。

## 4. 配置 PMMS

PMMS 是 MeQLab 的 MCP/gRPC 桥接程序，本仓库不分发其可执行文件。将 PMMS 安装到本机后：

1. 复制 `environment/pmms-config.example.txt` 为 PMMS 的实际配置文件。
2. 修改 MeQLab Linux 地址、用户名、SSH 私钥路径及启动脚本。
3. 在 `langgraph/.env` 中设置 `PMMS_EXE` 和 `PMMS_CONFIG`。

先测试连接：

```powershell
python -m scripts.test_pmms
```

## 5. 离线验证

```powershell
cd langgraph
python -m unittest discover -s tests -v
python main.py --demo --task "制定 GaN HEMT 参数提取计划"
```

## 6. 运行 LangGraph 五参数反向提取

确认 MeQLab 与 IC Linux 已启动，且模型 API 可连接，然后在 `langgraph` 目录执行：

```powershell
python -m scripts.run_langgraph_extraction `
  --config configs\asmhemt101_6_truth_blind_spectre.json `
  --state artifacts\asmhemt101_6\release_run_state.json `
  --agent-result artifacts\asmhemt101_6\release_run_result.json
```

流程只优化：

- `voff`
- `nfactor`
- `u0`
- `vsat`
- `lambda`

其他模型参数与器件实例参数保持固定。DeepSeek Agent 只能发起一次 `start_optimize`；LangGraph 会在同一工具节点中保持 MeQLab 会话并轮询到终态，随后读取最终误差和五个参数，再交给 Kimi 评审。

## 7. 完成判据

一次完整运行应满足：

1. `start_optimize` 只调用一次。
2. 同一个 Job 最终返回 `DONE`。
3. 最终 aggregate error 为有限值，且低于初始值 `0.553793116420489`。
4. 成功导出拟合模型和 view 文件。
5. 结果 JSON 包含 DeepSeek/Kimi 调用轨迹、最终误差和五个拟合参数。

## 常见问题

### 误差为 `nan`

先确认 MeQLab 使用的是 Spectre wrapper，而不是内置 NanoSpice。本项目验证表明，当前 MeQLab 自带 NanoSpice 无法可靠运行 ASM-HEMT 101.6.0 Verilog-A 模型。

### Agent 长时间显示 10%

每个候选参数组合都需要一次远程 Spectre 仿真，单次可能需要十几秒。当前代码会在 LangGraph 工具节点中持续轮询，不会因 DeepSeek 避免重复工具调用而提前结束。

### `APIConnectionError`

这表示模型网关网络不可达，不是 Spectre 拟合失败。先检查代理、DNS、网关状态和 API Base URL，再重新运行。不要在 API 不通时反复新建优化任务。

