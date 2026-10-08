from __future__ import annotations

import asyncio
import unittest

from eda_agent.blind import (
    filter_tool_specs,
    sanitize_tool_result,
)
from eda_agent.nodes import AgentNodes


class _Models:
    async def primary(self, state):
        return {}

    async def evaluate(self, state):
        return {}


class _PMMS:
    def __init__(self) -> None:
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return "参数列表:\n  - voff: -2.0\n  - u0: 0.17"


class BlindModeTests(unittest.TestCase):
    def test_sensitive_tools_are_not_exposed(self) -> None:
        specs = [
            {"name": "list_params"},
            {"name": "get_param"},
            {"name": "get_view_group_error"},
        ]
        self.assertEqual(
            [item["name"] for item in filter_tool_specs(specs)],
            ["get_view_group_error"],
        )

    def test_parameter_listing_is_redacted(self) -> None:
        result = sanitize_tool_result("参数列表:\n  - voff: -2.0\n  - u0: 0.17")
        self.assertNotIn("-2.0", result)
        self.assertIn("redaction", result)

    def test_sensitive_call_is_blocked_before_pmms(self) -> None:
        pmms = _PMMS()
        nodes = AgentNodes(_Models(), pmms, 8.0, blind_mode=True)
        result = asyncio.run(
            nodes.meqlab(
                {
                    "requested_tool": "list_params",
                    "requested_arguments": {"model_source_name": "hidden"},
                    "history": [],
                }
            )
        )
        self.assertEqual(pmms.calls, [])
        self.assertIn("Blind extraction blocks", result["error"])


if __name__ == "__main__":
    unittest.main()
