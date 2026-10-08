from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import replace
from pathlib import Path

from eda_agent.config import PROJECT_DIR, Settings
from eda_agent.modeling_pipeline import ModelingPipeline, PipelineConfig, PipelineState
from eda_agent.tools.pmms import PMMSClient


VIEW_CANDIDATES = (
    (
        "vg",
        "type,op,name,asmhemt101_6_truth_dc",
        "",
        "id_vgs",
        "x(-5,2),sep(5)",
    ),
    (
        "vg",
        "type,op,name,asmhemt101_6_truth_dc",
        "",
        "id_vg_vds",
        "x(-5,2),sep(5)",
    ),
    (
        "vg",
        "name,asmhemt101_6_truth_dc",
        "",
        "id_vgs",
        "x(-5,2),sep(5)",
    ),
    (
        "vg",
        "name,asmhemt101_6_truth_dc",
        "t=27.0",
        "id_vgs",
        "x(-5,2),sep(5)",
    ),
    (
        "vd",
        "type,op,name,asmhemt101_6_truth_dc",
        "",
        "id_vd_vgs",
        "x(0,10),sep(0)",
    ),
    (
        "vd",
        "name,asmhemt101_6_truth_dc",
        "",
        "id_vd_vgs",
        "x(0,10),sep(0)",
    ),
    (
        "vd",
        "name,asmhemt101_6_truth_dc",
        "t=27.0",
        "id_vd_vgs",
        "x(0,10),sep(0)",
    ),
)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=PROJECT_DIR / "configs" / "asmhemt101_6_truth_blind.json",
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=PROJECT_DIR / "artifacts" / "asmhemt101_6" / "truth_probe_state.json",
    )
    parser.add_argument("--project-name", default="ASMHEMT101_6_Truth_ViewProbe_20261005")
    parser.add_argument("--data-source-name", default="asmhemt101_6_truth_dc")
    parser.add_argument("--model-source-name", default="asmhemt_blind_N")
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=0,
        help="Stop after this many candidate views (0 checks every candidate).",
    )
    parser.add_argument(
        "--start-candidate",
        type=int,
        default=1,
        help="One-based candidate index at which probing begins.",
    )
    parser.add_argument(
        "--wait-seconds",
        type=float,
        default=600.0,
        help="Wait this long for an asynchronous view simulation to finish.",
    )
    parser.add_argument(
        "--model-wait-seconds",
        type=float,
        default=15.0,
        help="Allow asynchronous model-source validation to finish before views.",
    )
    args = parser.parse_args()

    settings = Settings.load(require_models=False)
    config = PipelineConfig.load(args.config.resolve())
    config = replace(config, project_name=args.project_name)
    state = PipelineState(args.state.resolve(), config)
    async with PMMSClient(
        settings.pmms_exe,
        settings.pmms_config,
        allow_destructive=True,
    ) as client:
        pipeline = ModelingPipeline(
            config,
            state,
            client,
            dry_run=False,
            allow_long_jobs=False,
        )
        await pipeline.run(("project", "data", "model"), resume=False)
        await asyncio.sleep(max(0.0, args.model_wait_seconds))
        await pipeline.run(("parameters", "filters"), resume=False)
        filters = await client.call_tool("list_filters", {})
        print("FILTERS")
        print(filters)

        start_index = max(0, args.start_candidate - 1)
        candidates = VIEW_CANDIDATES[start_index:]
        if args.max_candidates > 0:
            candidates = candidates[: args.max_candidates]
        for kind, filter_string, device_string, page, selection in candidates:
            await client.call_tool("clear_views", {})
            view_field = {
                "filter_string": filter_string,
                "source_string": f"{args.data_source_name}, {args.model_source_name}",
                "page_string": page,
                "selection_string": selection,
            }
            if device_string:
                view_field["device_string"] = device_string
            view_fields = [view_field]
            view_result = await client.call_tool("view", {"view_fields": view_fields})
            error_result = await client.call_tool("get_view_group_error", {})
            deadline = time.monotonic() + max(0.0, args.wait_seconds)
            while (
                "nan" in error_result.lower()
                and "共 0 个视图" not in error_result
                and time.monotonic() < deadline
            ):
                await asyncio.sleep(5.0)
                error_result = await client.call_tool("get_view_group_error", {})
            print(
                json.dumps(
                    {
                        "kind": kind,
                        "filter_string": filter_string,
                        "device_string": device_string,
                        "page_string": page,
                        "view_result": view_result,
                        "error_result": error_result,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )


if __name__ == "__main__":
    asyncio.run(main())
