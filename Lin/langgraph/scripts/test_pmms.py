from __future__ import annotations

import asyncio

from eda_agent.config import Settings
from eda_agent.tools import PMMSClient


async def main() -> None:
    settings = Settings.load(require_models=False)
    async with PMMSClient(settings.pmms_exe, settings.pmms_config) as pmms:
        print(f"PMMS connection OK. Tool count: {len(pmms.tool_names)}")
        for name in pmms.tool_names:
            print(f"- {name}")


if __name__ == "__main__":
    asyncio.run(main())

