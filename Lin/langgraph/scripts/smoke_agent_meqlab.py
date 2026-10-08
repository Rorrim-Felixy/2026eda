from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from eda_agent.config import Settings
from eda_agent.graph import build_graph
from eda_agent.tools import PMMSClient


class ReadOnlyProbeModels:
    """A deterministic graph probe that never spends LLM quota."""

    def __init__(self, tool_name: str, tool_args: dict[str, Any]) -> None:
        self.tool_name = tool_name
        self.tool_args = tool_args
        self.requested = False

    async def primary(self, state: dict[str, Any]) -> dict[str, Any]:
        if not self.requested:
            self.requested = True
            return {
                "summary": f"Call read-only MeQLab tool {self.tool_name}.",
                "answer": "Waiting for MeQLab.",
                "tool_name": self.tool_name,
                "tool_args": self.tool_args,
            }
        result = state.get("tool_result", "")
        error = state.get("error", "")
        return {
            "summary": "Return the real PMMS result without inventing data.",
            "answer": result if result else error,
            "tool_name": "",
            "tool_args": {},
        }

    async def evaluate(self, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "score": 10.0,
            "approved": True,
            "critique": "",
            "final_suggestion": state.get("proposed_answer", ""),
        }


async def run(tool_name: str, tool_args: dict[str, Any]) -> None:
    settings = Settings.load(require_models=False)
    async with PMMSClient(settings.pmms_exe, settings.pmms_config) as pmms:
        if tool_name not in pmms.tool_names:
            raise ValueError(f"PMMS does not expose {tool_name!r}")
        models = ReadOnlyProbeModels(tool_name, tool_args)
        graph = build_graph(models, pmms, approval_score=8.0)
        result = await graph.ainvoke(
            {
                "task": f"Run a read-only PMMS smoke test with {tool_name}",
                "iteration": 0,
                "review_round": 0,
                "max_review_rounds": 1,
                "tool_call_count": 0,
                "max_tool_calls": 1,
                "available_tools": pmms.tool_specs,
                "history": [],
                "critique": "",
                "tool_result": "",
                "error": "",
            }
        )
        print(f"PMMS connected: {len(pmms.tool_names)} tools")
        print(f"Graph tool calls: {result.get('tool_call_count', 0)}")
        print(result.get("final_answer", ""))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only Agent -> PMMS smoke test")
    parser.add_argument("--tool", default="list_variable")
    parser.add_argument(
        "--args-json", default="{}", help="JSON object matching the PMMS tool schema"
    )
    return parser.parse_args()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    parsed = json.loads(args.args_json)
    if not isinstance(parsed, dict):
        raise TypeError("--args-json must decode to a JSON object")
    asyncio.run(run(args.tool, parsed))
