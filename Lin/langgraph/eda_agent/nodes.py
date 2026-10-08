from __future__ import annotations

import asyncio
from dataclasses import dataclass
import re
from typing import Any, Literal

from eda_agent.models import ModelPair
from eda_agent.state import AgentState
from eda_agent.tools import PMMSClient
from eda_agent.blind import ensure_tool_allowed, sanitize_tool_result


@dataclass
class AgentNodes:
    models: ModelPair
    pmms: PMMSClient | None
    approval_score: float
    blind_mode: bool = True

    async def primary(self, state: AgentState) -> dict[str, Any]:
        output = await self.models.primary(state)
        iteration = state.get("iteration", 0) + 1
        history = list(state.get("history", []))
        requested_tool = str(output.get("tool_name", "") or "")
        requested_arguments = output.get("tool_args", {}) or {}

        # An optimizer job outlives an individual LLM response.  Some models
        # correctly avoid repeating an identical tool call and may therefore
        # try to summarize while MeQLab still reports RUNNING.  Keep the graph
        # attached to that same opaque job ID so the PMMS/MeQLab session is not
        # closed by the caller before optimization finishes.  This guard only
        # observes status; it never chooses or changes fitting parameters.
        starts = [
            (index, item)
            for index, item in enumerate(history)
            if item.get("role") == "tool"
            and item.get("tool") == "start_optimize"
            and item.get("ok") is True
        ]
        if not requested_tool and starts:
            start_index, start_event = starts[-1]
            match = re.search(
                r"Job ID:\s*([0-9a-f-]+)",
                str(start_event.get("result", "")),
            )
            job_id = match.group(1) if match else ""
            terminal = any(
                index > start_index
                and item.get("role") == "tool"
                and item.get("tool") == "get_job_status"
                and item.get("ok") is True
                and any(
                    marker in str(item.get("result", ""))
                    for marker in (
                        "Job 状态: DONE",
                        "Job 状态: FAILED",
                        "Job 状态: CANCELLED",
                    )
                )
                for index, item in enumerate(history)
            )
            if job_id and not terminal:
                requested_tool = "get_job_status"
                requested_arguments = {"job_id": job_id}
        history.append({"role": "primary", "iteration": iteration, "output": output})
        return {
            "iteration": iteration,
            "primary_summary": str(output.get("summary", "")),
            "proposed_answer": str(output.get("answer", "")),
            "requested_tool": requested_tool,
            "requested_arguments": requested_arguments,
            "history": history,
        }

    async def meqlab(self, state: AgentState) -> dict[str, Any]:
        tool_name = state.get("requested_tool", "")
        tool_arguments = dict(state.get("requested_arguments", {}))
        call_count = state.get("tool_call_count", 0) + 1
        history = list(state.get("history", []))
        if self.pmms is None:
            error = "The primary model requested a tool, but PMMS is disabled."
            history.append(
                {
                    "role": "tool",
                    "tool": tool_name,
                    "arguments": tool_arguments,
                    "ok": False,
                    "error": error,
                }
            )
            return {
                "tool_result": "",
                "error": error,
                "last_tool_name": tool_name,
                "last_tool_arguments": tool_arguments,
                "tool_call_count": call_count,
                "requested_tool": "",
                "requested_arguments": {},
                "history": history,
            }
        try:
            if self.blind_mode:
                ensure_tool_allowed(tool_name)
            if tool_name in {"start_optimize", "get_job_status", "get_view_group_error"}:
                starts = [
                    (index, item)
                    for index, item in enumerate(history)
                    if item.get("role") == "tool"
                    and item.get("tool") == "start_optimize"
                    and item.get("ok") is True
                ]
                if tool_name == "start_optimize" and starts:
                    raise RuntimeError(
                        "Only one optimization job is allowed in this extraction run. "
                        "Do not start another job; summarize the completed job and final error."
                    )
                if starts:
                    start_index, start_event = starts[-1]
                    match = re.search(
                        r"Job ID:\s*([0-9a-f-]+)",
                        str(start_event.get("result", "")),
                    )
                    active_job_id = match.group(1) if match else ""
                    done = any(
                        index > start_index
                        and item.get("role") == "tool"
                        and item.get("tool") == "get_job_status"
                        and item.get("ok") is True
                        and "Job 状态: DONE" in str(item.get("result", ""))
                        for index, item in enumerate(history)
                    )
                    if tool_name == "get_job_status" and active_job_id:
                        # Job IDs are opaque. Never allow an LLM transcription
                        # error to query a different or nonexistent job.
                        tool_arguments["job_id"] = active_job_id
                        latest_status = next(
                            (
                                item
                                for item in reversed(history)
                                if item.get("role") == "tool"
                                and item.get("tool") == "get_job_status"
                                and item.get("ok") is True
                            ),
                            None,
                        )
                        if latest_status and "Job 状态: RUNNING" in str(
                            latest_status.get("result", "")
                        ):
                            await asyncio.sleep(20)
                    if tool_name == "get_view_group_error" and not done:
                        raise RuntimeError(
                            "The active optimization job is not confirmed DONE. "
                            "Call get_job_status again with the recorded job ID."
                        )
            result = await self.pmms.call_tool(tool_name, tool_arguments)
            if tool_name == "get_job_status":
                # Status polling is transport bookkeeping, not a modeling
                # decision.  Keep it inside one LangGraph tool node so a
                # transient LLM/API outage cannot tear down the live MeQLab
                # session while Spectre is evaluating candidate parameters.
                for _ in range(90):
                    if "Job 状态: RUNNING" not in str(result):
                        break
                    await asyncio.sleep(20)
                    result = await self.pmms.call_tool(tool_name, tool_arguments)
                else:
                    raise TimeoutError(
                        "MeQLab optimization remained RUNNING for 30 minutes"
                    )
            if self.blind_mode:
                result = sanitize_tool_result(result)
            history.append(
                {
                    "role": "tool",
                    "tool": tool_name,
                    "arguments": tool_arguments,
                    "ok": True,
                    "result": result,
                }
            )
            return {
                "tool_result": result,
                "error": "",
                "last_tool_name": tool_name,
                "last_tool_arguments": tool_arguments,
                "tool_call_count": call_count,
                "requested_tool": "",
                "requested_arguments": {},
                "history": history,
            }
        except Exception as exc:
            error = f"MCP call failed: {exc}"
            history.append(
                {
                    "role": "tool",
                    "tool": tool_name,
                    "arguments": tool_arguments,
                    "ok": False,
                    "error": error,
                }
            )
            return {
                "tool_result": "",
                "error": error,
                "last_tool_name": tool_name,
                "last_tool_arguments": tool_arguments,
                "tool_call_count": call_count,
                "requested_tool": "",
                "requested_arguments": {},
                "history": history,
            }

    async def tool_limit(self, state: AgentState) -> dict[str, Any]:
        error = (
            "PMMS tool-call limit reached; the requested tool was not executed. "
            "Summarize the evidence already collected."
        )
        history = list(state.get("history", []))
        history.append(
            {
                "role": "system",
                "event": "tool_limit",
                "requested_tool": state.get("requested_tool", ""),
                "error": error,
            }
        )
        return {
            "error": error,
            "requested_tool": "",
            "requested_arguments": {},
            "history": history,
        }

    async def evaluator(self, state: AgentState) -> dict[str, Any]:
        output = await self.models.evaluate(state)
        review_round = state.get("review_round", 0) + 1
        score = float(output.get("score", 0.0))
        approved = bool(output.get("approved", False)) and score >= self.approval_score
        critique = str(output.get("critique", ""))
        history = list(state.get("history", []))
        history.append(
            {
                "role": "evaluator",
                "review_round": review_round,
                "iteration": state.get("iteration", 0),
                "score": score,
                "approved": approved,
                "critique": critique,
            }
        )
        return {
            "supervisor_score": score,
            "supervisor_approved": approved,
            "critique": critique,
            "review_round": review_round,
            "history": history,
        }

    async def finalize(self, state: AgentState) -> dict[str, Any]:
        return {"final_answer": state.get("proposed_answer", "")}

    @staticmethod
    def after_primary(
        state: AgentState,
    ) -> Literal["meqlab", "tool_limit", "evaluator"]:
        if not state.get("requested_tool"):
            return "evaluator"
        if state.get("tool_call_count", 0) >= state.get("max_tool_calls", 8):
            return "tool_limit"
        return "meqlab"

    @staticmethod
    def after_evaluator(state: AgentState) -> Literal["primary", "finalize"]:
        if state.get("supervisor_approved", False):
            return "finalize"
        if state.get("review_round", 0) >= state.get("max_review_rounds", 3):
            return "finalize"
        return "primary"
