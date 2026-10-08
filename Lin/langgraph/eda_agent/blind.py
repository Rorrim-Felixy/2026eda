from __future__ import annotations

import re
from typing import Any


# Tools that can reveal the model card, the current parameter values, or arbitrary
# remote files are never exposed to either LLM during a blind extraction run.
BLIND_FORBIDDEN_TOOLS = frozenset(
    {
        "download_file",
        "get_param",
        "list_params",
        "get_variable",
        "list_variable",
    }
)


def filter_tool_specs(tool_specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return only tools that are safe for a voltage/current-only LLM view."""
    return [
        spec
        for spec in tool_specs
        if str(spec.get("name", "")) not in BLIND_FORBIDDEN_TOOLS
    ]


def ensure_tool_allowed(tool_name: str) -> None:
    if tool_name in BLIND_FORBIDDEN_TOOLS:
        raise PermissionError(
            f"Blind extraction blocks PMMS tool '{tool_name}' because it can "
            "reveal hidden model parameters or files."
        )


_PARAMETER_LIST_RE = re.compile(
    r"(?is)(parameter\s+list|参数列表).*"
)
_ASSIGNMENT_RE = re.compile(
    r"(?im)^\s*[-*]?\s*[A-Za-z][A-Za-z0-9_]*\s*[:=]\s*"
    r"[-+]?\d(?:[\d.eE+-]*)\s*$"
)


def sanitize_tool_result(text: str) -> str:
    """Fail closed if an unexpected PMMS response contains parameter values."""
    if _PARAMETER_LIST_RE.search(text):
        return "[blind-mode redaction: model parameter listing withheld]"
    if len(_ASSIGNMENT_RE.findall(text)) >= 3:
        return "[blind-mode redaction: parameter-like assignments withheld]"
    return text
