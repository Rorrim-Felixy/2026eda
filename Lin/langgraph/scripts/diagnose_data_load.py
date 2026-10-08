from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from eda_agent.config import Settings
from eda_agent.tools import PMMSClient
from eda_agent.synthetic_data import write_dc_bias_template


DEFAULT_SAMPLE = (
    "/home/edaagent/AI-Agent/MS-MeQLab/MS-MeQLab/etc/demo/data/"
    "mosfet/nmos/iv/125/w=9.0,t=125.0,l=0.9.pms"
)


async def run(project: str, path: str, local_template: Path | None) -> None:
    settings = Settings.load(require_models=False)
    async with PMMSClient(settings.pmms_exe, settings.pmms_config) as pmms:
        print("generate_project:")
        print(
            await pmms.call_tool(
                "generate_project",
                {
                    "project_name": project,
                    "device_type": "Mosfet",
                    "device_polarity": "NMOS",
                },
            )
        )
        if local_template is not None:
            write_dc_bias_template(local_template)
            print("upload_file:")
            print(await pmms.upload_file(local_template, group_name="diagnostic"))
            path = f"diagnostic/{local_template.name}"
        print("load_data:")
        print(
            await pmms.call_tool(
                "load_data",
                {
                    "data_type": 0,
                    "path": path,
                    "data_source_name": "official_nmos_smoke",
                    "device_type": "Mosfet",
                    "device_polarity": "NMOS",
                },
            )
        )
        print("list_data_sources:")
        print(await pmms.call_tool("list_data_sources", {}))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="PMMS_Data_Load_Smoke")
    parser.add_argument("--path", default=DEFAULT_SAMPLE)
    parser.add_argument("--local-template", type=Path)
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    asyncio.run(run(arguments.project, arguments.path, arguments.local_template))
