import asyncio
from datetime import datetime
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


PROJECT_DIR = Path(__file__).resolve().parent
PMMS_PATH = PROJECT_DIR / "tools" / "mcp" / "pmms.exe"
SSH_KEY_PATH = Path.home() / ".ssh" / "meqlab_pmms"


def show_result(title: str, result) -> None:
    print(f"\n===== {title} =====")

    for content in result.content:
        text = getattr(content, "text", None)

        if text is not None:
            print(text)
        else:
            print(content)


async def main() -> None:
    # 使用时间生成不重复的测试工程名
    project_name = datetime.now().strftime("MCPTest%Y%m%d%H%M%S")

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

    print("正在连接PMMS和MeQLab……")

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            print("MCP初始化成功")

            # 1. 创建测试工程
            create_result = await session.call_tool(
                "generate_project",
                arguments={
                    "project_name": project_name,
                    "device_type": "Mosfet",
                    "device_polarity": "NMOS",
                },
            )
            show_result("创建工程", create_result)

            # 2. 检查工程是否存在
            exist_result = await session.call_tool(
                "exist_project",
                arguments={
                    "project_name": project_name,
                },
            )
            show_result("检查工程", exist_result)

            # 3. 保存工程
            save_result = await session.call_tool(
                "save_current_project",
                arguments={},
            )
            show_result("保存工程", save_result)

            # 4. 关闭工程
            close_result = await session.call_tool(
                "close_current_project",
                arguments={},
            )
            show_result("关闭工程", close_result)

    print("\n测试完成")
    print(f"测试工程名称：{project_name}")


if __name__ == "__main__":
    asyncio.run(main())