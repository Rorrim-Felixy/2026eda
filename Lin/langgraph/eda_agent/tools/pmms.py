from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Any

from mcp import Client, StdioServerParameters


class PMMSClient:
    """Maintains one persistent PMMS/MCP session for one graph run."""

    DESTRUCTIVE_TOOLS = {
        "remove_data_source",
        "remove_model_source",
        "remove_param",
        "remove_filter",
        "clear_devicecopy",
        "clear_views",
        "cancel_job",
    }
    READ_ONLY_TOOLS = {
        "exist_project",
        "list_variable",
        "get_variable",
        "list_data_sources",
        "get_data_detail",
        "list_available_models",
        "list_model_sources",
        "get_param",
        "list_params",
        "list_filters",
        "dump_view_group",
        "get_view_group_error",
        "get_job_status",
    }
    TRANSIENT_ERROR_MARKERS = (
        "another operation in progress",
        "retry later",
    )
    TOOL_ERROR_MARKERS = (
        "tool 执行失败",
        "tool execution failed",
    )

    @staticmethod
    def _trace_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
        """Keep traces useful without retaining uploaded base64 payloads."""
        cleaned = dict(arguments)
        chunk = cleaned.get("chunk_data")
        if isinstance(chunk, str):
            cleaned["chunk_data"] = f"<base64 omitted; {len(chunk)} chars>"
        return cleaned

    def __init__(
        self,
        executable: Path,
        config_file: Path,
        *,
        allow_destructive: bool = False,
        read_retries: int = 2,
    ) -> None:
        self.executable = executable.resolve()
        self.config_file = config_file.resolve()
        self.allow_destructive = allow_destructive
        self.read_retries = max(0, read_retries)
        self._client: Client | None = None
        self.tool_names: list[str] = []
        self.tool_specs: list[dict[str, Any]] = []
        self.trace: list[dict[str, Any]] = []

    async def __aenter__(self) -> "PMMSClient":
        if not self.executable.is_file():
            raise FileNotFoundError(f"PMMS executable not found: {self.executable}")
        if not self.config_file.is_file():
            raise FileNotFoundError(f"PMMS config not found: {self.config_file}")

        parameters = StdioServerParameters(
            command=str(self.executable),
            args=["--config", str(self.config_file)],
            cwd=self.executable.parent,
        )
        self._client = Client(parameters, mode="legacy", read_timeout_seconds=90)
        await self._client.__aenter__()
        result = await self._client.list_tools()
        self.tool_names = [tool.name for tool in result.tools]
        self.tool_specs = [
            {
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": tool.input_schema,
            }
            for tool in result.tools
        ]
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._client is not None:
            await self._client.__aexit__(exc_type, exc, traceback)
            self._client = None
        self.tool_names = []
        self.tool_specs = []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        if self._client is None:
            raise RuntimeError("PMMS client is not connected")
        if name not in self.tool_names:
            raise ValueError(f"PMMS does not expose tool: {name}")

        if name in self.DESTRUCTIVE_TOOLS and not self.allow_destructive:
            raise PermissionError(
                f"Refusing destructive PMMS tool '{name}'. "
                "Create PMMSClient(..., allow_destructive=True) only after explicit approval."
            )

        attempts = self.read_retries + 1 if name in self.READ_ONLY_TOOLS else 1
        for attempt in range(attempts):
            event: dict[str, Any] = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "tool": name,
                "arguments": self._trace_arguments(arguments),
                "attempt": attempt + 1,
            }
            try:
                result = await self._client.call_tool(name, arguments)
                parts: list[str] = []
                for block in result.content:
                    text = getattr(block, "text", None)
                    parts.append(text if text else str(block))
                rendered = "\n".join(parts)
                result_error = bool(getattr(result, "is_error", False))
                lowered = rendered.lower()
                transient = any(
                    marker in lowered for marker in self.TRANSIENT_ERROR_MARKERS
                )
                reported_error = any(
                    marker in lowered for marker in self.TOOL_ERROR_MARKERS
                )
                event["result"] = rendered
                event["ok"] = not result_error and not reported_error and not transient
                self.trace.append(event)
                if transient and attempt + 1 < attempts:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                if result_error or reported_error:
                    raise RuntimeError(rendered)
                return rendered
            except Exception as exc:
                event.setdefault("ok", False)
                event["error"] = str(exc)
                if not self.trace or self.trace[-1] is not event:
                    self.trace.append(event)
                raise

        raise RuntimeError(f"PMMS call failed without a result: {name}")

    async def upload_file(
        self,
        path: Path,
        *,
        group_name: str | None = None,
        chunk_size: int = 1024 * 1024,
    ) -> str:
        """Upload one local file through PMMS using the documented chunk protocol."""
        source = path.resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        if chunk_size < 1 or chunk_size > 1024 * 1024:
            raise ValueError("chunk_size must be between 1 byte and 1 MiB")

        data = source.read_bytes()
        chunks = [data[i : i + chunk_size] for i in range(0, len(data), chunk_size)]
        if not chunks:
            chunks = [b""]

        last_result = ""
        for index, chunk in enumerate(chunks):
            arguments: dict[str, Any] = {
                "file_name": source.name,
                "chunk_data": base64.b64encode(chunk).decode("ascii"),
                "is_last": index == len(chunks) - 1,
            }
            if group_name:
                arguments["group_name"] = group_name
            last_result = await self.call_tool("upload_file", arguments)
        return last_result

    async def download_file(self, remote_path: str, destination: Path) -> Path:
        """Download a PMMS server file and decode the documented JSON payload."""
        rendered = await self.call_tool("download_file", {"path": remote_path})
        payload = json.loads(rendered)
        encoded = payload.get("data")
        if not isinstance(encoded, str):
            raise ValueError(f"download_file returned no base64 data: {rendered[:500]}")
        target = destination.resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(encoded))
        return target
