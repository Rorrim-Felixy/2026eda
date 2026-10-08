from __future__ import annotations

import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eda_agent.graph import build_graph
from eda_agent.tools import PMMSClient


class ScriptedModels:
    """Deterministic model pair proving the tool result returns to the planner."""

    def __init__(self) -> None:
        self.primary_calls = 0
        self.seen_tool_result = ""

    async def primary(self, state: dict[str, Any]) -> dict[str, Any]:
        self.primary_calls += 1
        if self.primary_calls == 1:
            return {
                "summary": "Read the current MeQLab variables.",
                "answer": "Waiting for MeQLab evidence.",
                "tool_name": "list_variable",
                "tool_args": {},
            }
        self.seen_tool_result = state.get("tool_result", "")
        return {
            "summary": "Interpret the returned variables.",
            "answer": f"MeQLab returned: {self.seen_tool_result}",
            "tool_name": "",
            "tool_args": {},
        }

    async def evaluate(self, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "score": 9.5,
            "approved": "VGS" in state.get("proposed_answer", ""),
            "critique": "",
            "final_suggestion": state.get("proposed_answer", ""),
        }


class FakePMMS:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        self.calls.append((name, arguments))
        return '[{"name":"VGS","value":"-3.0"}]'


class MeQLabWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_result_returns_to_primary_before_supervision(self) -> None:
        models = ScriptedModels()
        pmms = FakePMMS()
        graph = build_graph(models, pmms, approval_score=8.0, blind_mode=False)
        result = await graph.ainvoke(
            {
                "task": "Inspect MeQLab variables",
                "iteration": 0,
                "review_round": 0,
                "max_review_rounds": 2,
                "tool_call_count": 0,
                "max_tool_calls": 3,
                "available_tools": [
                    {"name": "list_variable", "description": "", "input_schema": {}}
                ],
                "history": [],
                "critique": "",
                "tool_result": "",
                "error": "",
            }
        )

        self.assertEqual(pmms.calls, [("list_variable", {})])
        self.assertEqual(models.primary_calls, 2)
        self.assertIn("VGS", models.seen_tool_result)
        self.assertIn("VGS", result["final_answer"])
        self.assertEqual(result["tool_call_count"], 1)
        self.assertTrue(result["supervisor_approved"])

    async def test_destructive_tool_is_denied_by_default(self) -> None:
        client = PMMSClient(Path("pmms.exe"), Path("config.txt"))
        client._client = object()  # type: ignore[assignment]
        client.tool_names = ["remove_param"]

        with self.assertRaises(PermissionError):
            await client.call_tool("remove_param", {"name": "voff"})

    def test_upload_payload_is_redacted_from_trace(self) -> None:
        arguments = {"file_name": "model.va", "chunk_data": "QUJD", "is_last": True}
        traced = PMMSClient._trace_arguments(arguments)

        self.assertEqual(arguments["chunk_data"], "QUJD")
        self.assertEqual(traced["chunk_data"], "<base64 omitted; 4 chars>")

    async def test_transient_read_is_retried(self) -> None:
        @dataclass
        class TextBlock:
            text: str

        @dataclass
        class Result:
            content: list[TextBlock]
            is_error: bool = False

        class RetryClient:
            def __init__(self) -> None:
                self.calls = 0

            async def call_tool(self, name: str, arguments: dict[str, Any]) -> Result:
                self.calls += 1
                if self.calls == 1:
                    return Result([TextBlock("another operation in progress, retry later")])
                return Result([TextBlock("VGS=-3.0")])

        inner = RetryClient()
        client = PMMSClient(
            Path("pmms.exe"), Path("config.txt"), read_retries=1
        )
        client._client = inner  # type: ignore[assignment]
        client.tool_names = ["list_variable"]

        result = await client.call_tool("list_variable", {})

        self.assertEqual(result, "VGS=-3.0")
        self.assertEqual(inner.calls, 2)
        self.assertEqual([event["ok"] for event in client.trace], [False, True])

    async def test_pmms_text_error_is_not_treated_as_success(self) -> None:
        @dataclass
        class TextBlock:
            text: str

        @dataclass
        class Result:
            content: list[TextBlock]
            is_error: bool = False

        class ErrorClient:
            async def call_tool(self, name: str, arguments: dict[str, Any]) -> Result:
                return Result([TextBlock("错误：Tool 执行失败，错误码：1")])

        client = PMMSClient(
            Path("pmms.exe"), Path("config.txt"), read_retries=0
        )
        client._client = ErrorClient()  # type: ignore[assignment]
        client.tool_names = ["list_variable"]

        with self.assertRaisesRegex(RuntimeError, "错误码"):
            await client.call_tool("list_variable", {})


if __name__ == "__main__":
    unittest.main()
