from __future__ import annotations

import asyncio
import json

from eda_agent.config import Settings
from eda_agent.tools import PMMSClient


async def main() -> None:
    settings = Settings.load(require_models=False)
    async with PMMSClient(settings.pmms_exe, settings.pmms_config) as pmms:
        selected = []
        for spec in pmms.tool_specs:
            haystack = f"{spec['name']} {spec['description']}".lower()
            if any(
                token in haystack
                for token in ("data", "page", "view", "filter", "param", "project", "model")
            ):
                selected.append(spec)
        print(json.dumps(selected, ensure_ascii=False, indent=2))
        if pmms._client is not None:
            resource = await pmms._client.read_resource("pmms://view_fields_guide")
            print("\nVIEW_FIELDS_GUIDE\n")
            for item in resource.contents:
                print(json.dumps(getattr(item, "text", str(item)), ensure_ascii=True))


if __name__ == "__main__":
    asyncio.run(main())
