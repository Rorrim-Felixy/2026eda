import asyncio
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


PROJECT_DIR = Path(__file__).resolve().parent
PMMS_PATH = PROJECT_DIR / "tools" / "mcp" / "pmms.exe"
SSH_KEY_PATH = Path.home() / ".ssh" / "meqlab_pmms"


async def main() -> None:
    if not PMMS_PATH.exists():
        raise FileNotFoundError(f"找不到PMMS：{PMMS_PATH}")

    if not SSH_KEY_PATH.exists():
        raise FileNotFoundError(f"找不到SSH密钥：{SSH_KEY_PATH}")

    print("正在启动PMMS并连接Ubuntu中的MeQLab……")

    server_params = StdioServerParameters(
        command=str(PMMS_PATH),
        args=[
            "--ssh-host",
            "192.168.198.128",
            "--ssh-user",
            "user",
            "--ssh-key",
            str(SSH_KEY_PATH),
            "--meqlab-path",
            "/home/user/start-meqlab.sh",
            "--feature",
            "prim|rf_main|ppei",
        ],
        cwd=str(PROJECT_DIR),
    )

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            print("正在进行MCP初始化……")
            await session.initialize()

            print("初始化成功，正在读取工具列表……")
            tools_result = await session.list_tools()

            print(f"PMMS共提供 {len(tools_result.tools)} 个工具：")

            for index, tool in enumerate(tools_result.tools, start=1):
                print(f"{index:02d}. {tool.name}")


if __name__ == "__main__":
    asyncio.run(main())