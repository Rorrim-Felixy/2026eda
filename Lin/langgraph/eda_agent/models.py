from __future__ import annotations

import asyncio
import json
from typing import Any, Protocol

from openai import APIConnectionError, APITimeoutError, AsyncOpenAI, RateLimitError

from eda_agent.config import Settings
from eda_agent.prompts import EVALUATOR_SYSTEM_PROMPT, PRIMARY_SYSTEM_PROMPT
from eda_agent.state import AgentState


class ModelPair(Protocol):
    async def primary(self, state: AgentState) -> dict[str, Any]: ...

    async def evaluate(self, state: AgentState) -> dict[str, Any]: ...


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError(f"Model did not return a JSON object: {text[:300]}")
    return json.loads(text[start : end + 1])


class OpenAIDualModelService:
    """Calls two OpenAI-compatible model IDs through one provider endpoint."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.call_trace: list[dict[str, Any]] = []
        self.client = AsyncOpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
        )

    async def _json_call(
        self,
        model: str,
        system_prompt: str,
        payload: dict[str, Any],
        temperature: float,
        purpose: str,
    ) -> dict[str, Any]:
        for attempt in range(6):
            try:
                response = await self.client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {
                            "role": "user",
                            "content": json.dumps(payload, ensure_ascii=False),
                        },
                    ],
                    temperature=temperature,
                )
                break
            except (APIConnectionError, APITimeoutError, RateLimitError):
                if attempt == 5:
                    raise
                await asyncio.sleep(min(60, 5 * (2**attempt)))
        usage = response.usage
        self.call_trace.append(
            {
                "purpose": purpose,
                "model": model,
                "prompt_tokens": getattr(usage, "prompt_tokens", None),
                "completion_tokens": getattr(usage, "completion_tokens", None),
                "total_tokens": getattr(usage, "total_tokens", None),
            }
        )
        return _extract_json(response.choices[0].message.content or "")

    async def primary(self, state: AgentState) -> dict[str, Any]:
        return await self._json_call(
            self.settings.primary_model,
            PRIMARY_SYSTEM_PROMPT,
            {
                "task": state["task"],
                "iteration": state.get("iteration", 0),
                "supervisor_critique": state.get("critique", ""),
                "meqlab_result": state.get("tool_result", ""),
                "last_tool_name": state.get("last_tool_name", ""),
                "last_tool_arguments": state.get("last_tool_arguments", {}),
                "tool_call_count": state.get("tool_call_count", 0),
                "mcp_error": state.get("error", ""),
                "recent_history": state.get("history", [])[-12:],
                "available_tools": state.get("available_tools", []),
            },
            temperature=0.25,
            purpose="primary",
        )

    async def evaluate(self, state: AgentState) -> dict[str, Any]:
        payload = {
            "task": state["task"],
            "iteration": state.get("iteration", 0),
            "primary_summary": state.get("primary_summary", ""),
            "proposed_answer": state.get("proposed_answer", ""),
            "meqlab_result": state.get("tool_result", ""),
            "last_tool_name": state.get("last_tool_name", ""),
            "mcp_error": state.get("error", ""),
        }
        return await self._json_call(
            self.settings.evaluator_model,
            EVALUATOR_SYSTEM_PROMPT,
            payload,
            temperature=0.0,
            purpose="evaluator",
        )


class DemoDualModelService:
    """Offline stand-ins used to test graph routing without spending API quota."""

    async def primary(self, state: AgentState) -> dict[str, Any]:
        if state.get("iteration", 0) == 0:
            return {
                "summary": "Create an initial modeling plan.",
                "answer": "Initial plan without a measurable validation rule.",
                "tool_name": "",
                "tool_args": {},
            }
        return {
            "summary": "Revise the plan using the supervisor critique.",
            "answer": (
                "Revised plan with a measurable validation rule. Critique used: "
                + state.get("critique", "")
            ),
            "tool_name": "",
            "tool_args": {},
        }

    async def evaluate(self, state: AgentState) -> dict[str, Any]:
        if state.get("iteration", 0) < 2:
            return {
                "score": 5.0,
                "approved": False,
                "critique": "Add an explicit error threshold and validation step.",
                "final_suggestion": "",
            }
        return {
            "score": 9.0,
            "approved": True,
            "critique": "The revised plan is sufficiently specific.",
            "final_suggestion": state.get("proposed_answer", ""),
        }
