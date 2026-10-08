# 部署拓扑模板

```text
Windows / LangGraph
  |
  | PMMS: SSH + gRPC
  v
MeQLab Linux
  |
  | spectre wrapper: SSH/SCP
  v
IC Linux / Cadence Spectre
```

本次验证环境曾使用私有网段地址，但仓库配置应改为自己的地址：

| 节点 | 必需软件 | 配置项 |
|---|---|---|
| Windows | Python、LangGraph、PMMS | `.env`、PMMS config |
| MeQLab Linux | MeQLab、OpenSSH client、wrapper | `MEQLAB_SPECTRE_*` |
| IC Linux | Cadence Spectre、许可证、OpenSSH server | Spectre 与 license 路径 |

需要两条认证链路：

1. Windows 到 MeQLab Linux，用于 PMMS 启动 MeQLab。
2. MeQLab Linux 到 IC Linux，用于 wrapper 提交 Spectre 作业。

只使用 SSH 公钥认证。不要把任何私钥复制进仓库。

