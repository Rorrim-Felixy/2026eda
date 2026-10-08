from __future__ import annotations

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    """Shared state passed between every node in the graph."""

    task: str
    iteration: int
    review_round: int
    max_review_rounds: int
    tool_call_count: int
    max_tool_calls: int
    available_tools: list[dict[str, Any]]

    primary_summary: str
    proposed_answer: str
    requested_tool: str
    requested_arguments: dict[str, Any]

    tool_result: str
    last_tool_name: str
    last_tool_arguments: dict[str, Any]
    error: str

    supervisor_score: float
    supervisor_approved: bool
    critique: str

    history: list[dict[str, Any]]
    final_answer: str
