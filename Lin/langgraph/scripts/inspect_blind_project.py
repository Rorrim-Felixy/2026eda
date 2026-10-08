from __future__ import annotations

import asyncio

from eda_agent.config import Settings
from eda_agent.tools.pmms import PMMSClient


PROJECT = "ASMHEMT101_6_Blind_Stage1_20261004"


async def main() -> None:
    settings = Settings.load(require_models=False)
    async with PMMSClient(settings.pmms_exe, settings.pmms_config) as client:
        print(await client.call_tool("open_project", {"project_name": PROJECT}))
        print(await client.call_tool("list_data_sources", {}))
        print(await client.call_tool("list_filters", {}))


if __name__ == "__main__":
    asyncio.run(main())
