from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import AsyncExitStack

from eda_agent.config import Settings
from eda_agent.blind import filter_tool_specs
from eda_agent.graph import build_graph
from eda_agent.models import DemoDualModelService, OpenAIDualModelService
from eda_agent.tools import PMMSClient


async def run(args: argparse.Namespace) -> None:
    settings = Settings.load(require_models=not args.demo)
    models = DemoDualModelService() if args.demo else OpenAIDualModelService(settings)

    if not args.demo and settings.primary_model == settings.evaluator_model:
        print(
            "WARNING: the primary and evaluator model IDs are identical; "
            "configure different IDs for stronger independent review."
        )

    async with AsyncExitStack() as stack:
        pmms = None
        tool_specs = []
        if args.with_pmms:
            pmms = await stack.enter_async_context(
                PMMSClient(settings.pmms_exe, settings.pmms_config)
            )
            tool_specs = filter_tool_specs(pmms.tool_specs)
            print(f"PMMS connected: {len(tool_specs)} tools")

        graph = build_graph(
            models,
            pmms,
            settings.approval_score,
            blind_mode=True,
        )
        result = await graph.ainvoke(
            {
                "task": args.task,
                "iteration": 0,
                "review_round": 0,
                "max_review_rounds": settings.max_review_rounds,
                "tool_call_count": 0,
                "max_tool_calls": settings.max_tool_calls,
                "available_tools": tool_specs,
                "history": [],
                "critique": "",
                "tool_result": "",
                "error": "",
            }
        )
        if isinstance(models, OpenAIDualModelService):
            result["model_call_trace"] = models.call_trace
        print(json.dumps(result, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dual-model LangGraph MeQLab agent")
    parser.add_argument("--task", required=True, help="Task given to the agent")
    parser.add_argument(
        "--demo", action="store_true", help="Use offline mock models; no API quota"
    )
    parser.add_argument(
        "--with-pmms", action="store_true", help="Connect to PMMS/MeQLab tools"
    )
    return parser.parse_args()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(run(parse_args()))
