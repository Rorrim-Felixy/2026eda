from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol


PIPELINE_STAGES = (
    "project",
    "data",
    "model",
    "parameters",
    "filters",
    "views",
    "optimize",
    "qa",
    "export",
)


class ToolClient(Protocol):
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str: ...

    async def upload_file(
        self, path: Path, *, group_name: str | None = None
    ) -> str: ...


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _relative_uploaded_path(path: Path, group_name: str | None) -> str:
    return f"{group_name}/{path.name}" if group_name else path.name


@dataclass(frozen=True)
class DataSourceConfig:
    name: str
    path: str
    data_type: int = 0
    device_type: str = "Mosfet"
    device_polarity: str = "NMOS"
    extract_spec: bool = True
    local_path: str | None = None
    upload_group: str | None = "data"


@dataclass(frozen=True)
class ModelConfig:
    path: str
    format: str = "hspice"
    suite_id: int | None = None
    lib_name: str | None = None
    model_name: str | None = None
    simulator: str = "Nano"
    source_name: str | None = None
    local_path: str | None = None
    upload_group: str | None = "model"


@dataclass(frozen=True)
class ParameterConfig:
    name: str
    value: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    node_name: str | None = None
    const_index: int = 0


@dataclass(frozen=True)
class FilterConfig:
    kind: str = "data"
    data_source_name: str | None = None
    template_path: str | None = None
    local_template_path: str | None = None
    upload_group: str | None = "filters"


@dataclass(frozen=True)
class OptimizeConfig:
    enabled: bool = False
    param_string: str = ""


@dataclass(frozen=True)
class QAConfig:
    enabled: bool = False


@dataclass(frozen=True)
class ExportConfig:
    save_project: bool = True
    model_path: str | None = None
    view_group_path: str | None = None
    report_path: str | None = None


@dataclass(frozen=True)
class PipelineConfig:
    project_name: str
    device_type: str = "Mosfet"
    device_polarity: str = "NMOS"
    create_project: bool = False
    variables: dict[str, str] = field(default_factory=dict)
    data_sources: tuple[DataSourceConfig, ...] = ()
    model: ModelConfig | None = None
    parameters: tuple[ParameterConfig, ...] = ()
    filters: tuple[FilterConfig, ...] = ()
    views: tuple[dict[str, Any], ...] = ()
    optimize: OptimizeConfig = field(default_factory=OptimizeConfig)
    qa: QAConfig = field(default_factory=QAConfig)
    export: ExportConfig = field(default_factory=ExportConfig)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PipelineConfig":
        if not raw.get("project_name"):
            raise ValueError("project_name is required")
        config = cls(
            project_name=str(raw["project_name"]),
            device_type=str(raw.get("device_type", "Mosfet")),
            device_polarity=str(raw.get("device_polarity", "NMOS")),
            create_project=bool(raw.get("create_project", False)),
            variables={str(k): str(v) for k, v in raw.get("variables", {}).items()},
            data_sources=tuple(
                DataSourceConfig(**item) for item in raw.get("data_sources", [])
            ),
            model=ModelConfig(**raw["model"]) if raw.get("model") else None,
            parameters=tuple(
                ParameterConfig(**item) for item in raw.get("parameters", [])
            ),
            filters=tuple(FilterConfig(**item) for item in raw.get("filters", [])),
            views=tuple(dict(item) for item in raw.get("views", [])),
            optimize=OptimizeConfig(**raw.get("optimize", {})),
            qa=QAConfig(**raw.get("qa", {})),
            export=ExportConfig(**raw.get("export", {})),
        )
        config.validate()
        return config

    @classmethod
    def load(cls, path: Path) -> "PipelineConfig":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def validate(self) -> None:
        if self.device_polarity not in {"NMOS", "PMOS"}:
            raise ValueError("device_polarity must be NMOS or PMOS")
        for source in self.data_sources:
            if source.data_type not in {0, 1, 2}:
                raise ValueError(f"invalid data_type for {source.name}: {source.data_type}")
        for item in self.filters:
            if item.kind not in {"data", "template"}:
                raise ValueError(f"unsupported filter kind: {item.kind}")
            if item.kind == "data" and not item.data_source_name:
                raise ValueError("data filter requires data_source_name")
            if item.kind == "template" and not (
                item.template_path or item.local_template_path
            ):
                raise ValueError("template filter requires a template path")
        if self.optimize.enabled and not self.optimize.param_string.strip():
            raise ValueError("enabled optimization requires param_string")
        if self.views and any(not item.get("selection_string") for item in self.views):
            if self.optimize.enabled:
                raise ValueError(
                    "every optimization view requires a non-empty selection_string"
                )

    def fingerprint(self) -> str:
        return _json_hash(asdict(self))


class PipelineState:
    """Small JSON journal used for review, restart and safe stage resumption."""

    def __init__(self, path: Path, config: PipelineConfig) -> None:
        self.path = path.resolve()
        self.config = config
        self.data: dict[str, Any] = {
            "schema_version": 1,
            "config_hash": config.fingerprint(),
            "project_name": config.project_name,
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
            "stages": {},
            "resolved": {},
            "actions": [],
        }

    def load_for_resume(self) -> None:
        if not self.path.is_file():
            return
        loaded = json.loads(self.path.read_text(encoding="utf-8"))
        if loaded.get("config_hash") != self.config.fingerprint():
            raise RuntimeError(
                "state file belongs to a different configuration; use a new state path"
            )
        self.data = loaded

    def stage_status(self, stage: str) -> str | None:
        return self.data.get("stages", {}).get(stage, {}).get("status")

    def set_stage(self, stage: str, status: str, error: str | None = None) -> None:
        entry = self.data.setdefault("stages", {}).setdefault(stage, {})
        entry["status"] = status
        entry["updated_at"] = _utc_now()
        if error:
            entry["error"] = error
        else:
            entry.pop("error", None)
        self.save()

    def resolve(self, key: str, value: Any) -> None:
        self.data.setdefault("resolved", {})[key] = value
        self.save()

    def resolved(self, key: str, default: Any = None) -> Any:
        return self.data.get("resolved", {}).get(key, default)

    def record(
        self,
        stage: str,
        tool: str,
        arguments: dict[str, Any],
        *,
        status: str,
        result: str | None = None,
        error: str | None = None,
    ) -> None:
        event: dict[str, Any] = {
            "timestamp": _utc_now(),
            "stage": stage,
            "tool": tool,
            "arguments": arguments,
            "status": status,
        }
        if result is not None:
            event["result"] = result[:4000]
        if error is not None:
            event["error"] = error
        self.data.setdefault("actions", []).append(event)
        self.save()

    def save(self) -> None:
        self.data["updated_at"] = _utc_now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temp.replace(self.path)


class ModelingPipeline:
    def __init__(
        self,
        config: PipelineConfig,
        state: PipelineState,
        client: ToolClient | None = None,
        *,
        dry_run: bool = True,
        allow_long_jobs: bool = False,
    ) -> None:
        if not dry_run and client is None:
            raise ValueError("a connected PMMS client is required outside dry-run")
        self.config = config
        self.state = state
        self.client = client
        self.dry_run = dry_run
        self.allow_long_jobs = allow_long_jobs

    async def _call(self, stage: str, tool: str, arguments: dict[str, Any]) -> str:
        if self.dry_run:
            self.state.record(stage, tool, arguments, status="planned")
            return "<dry-run>"
        assert self.client is not None
        try:
            result = await self.client.call_tool(tool, arguments)
            self.state.record(
                stage, tool, arguments, status="completed", result=result
            )
            return result
        except Exception as exc:
            self.state.record(
                stage, tool, arguments, status="failed", error=str(exc)
            )
            raise

    async def _upload(
        self, stage: str, local_path: str, group_name: str | None
    ) -> str:
        source = Path(local_path).expanduser().resolve()
        arguments = {"local_path": str(source), "group_name": group_name}
        if self.dry_run:
            self.state.record(stage, "upload_file", arguments, status="planned")
            return _relative_uploaded_path(source, group_name)
        if not source.is_file():
            raise FileNotFoundError(source)
        assert self.client is not None
        try:
            result = await self.client.upload_file(source, group_name=group_name)
            self.state.record(
                stage, "upload_file", arguments, status="completed", result=result
            )
            return _relative_uploaded_path(source, group_name)
        except Exception as exc:
            self.state.record(
                stage, "upload_file", arguments, status="failed", error=str(exc)
            )
            raise

    async def run(
        self,
        stages: Iterable[str] = PIPELINE_STAGES,
        *,
        resume: bool = False,
    ) -> PipelineState:
        selected = list(stages)
        unknown = set(selected) - set(PIPELINE_STAGES)
        if unknown:
            raise ValueError(f"unknown stages: {', '.join(sorted(unknown))}")
        if resume:
            self.state.load_for_resume()

        for stage in selected:
            if resume and self.state.stage_status(stage) == "completed":
                continue
            self.state.set_stage(stage, "running")
            try:
                await getattr(self, f"_stage_{stage}")()
            except Exception as exc:
                self.state.set_stage(stage, "failed", str(exc))
                raise
            self.state.set_stage(
                stage, "planned" if self.dry_run else "completed"
            )
        return self.state

    async def refresh_job_status(self, kind: str) -> str:
        """Fetch one status snapshot; intentionally does not busy-poll long jobs."""
        if kind not in {"optimize", "qa"}:
            raise ValueError("kind must be optimize or qa")
        job_id = self.state.resolved(f"{kind}_job_id")
        if not job_id:
            raise RuntimeError(f"no recorded {kind} job ID")
        result = await self._call(
            "job_status", "get_job_status", {"job_id": job_id}
        )
        status = "PLANNED" if self.dry_run else self._parse_job_status(result)
        if not status:
            raise RuntimeError(f"cannot parse {kind} job status")
        self.state.resolve(f"{kind}_job_status", status)
        return status

    async def _stage_project(self) -> None:
        if self.config.create_project:
            await self._call(
                "project",
                "generate_project",
                {
                    "project_name": self.config.project_name,
                    "device_type": self.config.device_type,
                    "device_polarity": self.config.device_polarity,
                },
            )
        else:
            await self._call(
                "project", "open_project", {"project_name": self.config.project_name}
            )
        for name, value in self.config.variables.items():
            await self._call(
                "project", "set_variable", {"name": name, "value": value}
            )

    async def _stage_data(self) -> None:
        for source in self.config.data_sources:
            path = source.path
            if source.local_path:
                path = await self._upload("data", source.local_path, source.upload_group)
            await self._call(
                "data",
                "load_data",
                {
                    "data_type": source.data_type,
                    "path": path,
                    "data_source_name": source.name,
                    "device_type": source.device_type,
                    "device_polarity": source.device_polarity,
                },
            )
            # Force MeQLab to materialize/refresh its source registry before a
            # name-based detail lookup. Some PMS imports report success before
            # the registry view is populated.
            await self._call("data", "list_data_sources", {})
            await self._call(
                "data", "get_data_detail", {"data_source_name": source.name}
            )
            if source.extract_spec and source.data_type == 0:
                await self._call(
                    "data", "extract_spec", {"data_source_name": source.name}
                )
        await self._call("data", "list_data_sources", {})

    async def _stage_model(self) -> None:
        model = self.config.model
        if model is None:
            return
        path = model.path
        if model.local_path:
            path = await self._upload("model", model.local_path, model.upload_group)
        load_result = await self._call(
            "model", "load_model", {"path": path, "format": model.format}
        )
        available = await self._call("model", "list_available_models", {})
        suite_id = model.suite_id
        if suite_id is None and not self.dry_run:
            suite_id = self._parse_suite_id(available) or self._parse_suite_id(load_result)
        if suite_id is None:
            if self.dry_run:
                suite_id = 0
            else:
                raise RuntimeError("cannot determine model_suite_id; set model.suite_id")
        self.state.resolve("model_suite_id", suite_id)
        arguments: dict[str, Any] = {
            "model_suite_id": suite_id,
            "simulator": model.simulator,
        }
        if model.lib_name:
            arguments["lib_name"] = model.lib_name
        if model.model_name:
            arguments["model_name"] = model.model_name
        add_result = await self._call("model", "add_model_source", arguments)
        sources = await self._call("model", "list_model_sources", {})
        source_name = model.source_name
        if source_name is None and not self.dry_run:
            source_name = self._parse_source_name(sources) or self._parse_source_name(
                add_result
            )
        if source_name is None and self.dry_run:
            source_name = "<resolved-model-source>"
        if source_name:
            self.state.resolve("model_source_name", source_name)
        elif not self.dry_run:
            raise RuntimeError("cannot determine model_source_name; set model.source_name")

    async def _stage_parameters(self) -> None:
        source_name = self._model_source_name()
        if not source_name and self.config.parameters:
            raise RuntimeError("model source is required before setting parameters")
        for param in self.config.parameters:
            arguments: dict[str, Any] = {
                "model_source_name": source_name,
                "param_name": param.name,
                "const_index": param.const_index,
            }
            if param.node_name is not None:
                arguments["node_name"] = param.node_name
            if param.value is not None:
                arguments["param_value"] = param.value
            if param.minimum is not None:
                arguments["param_min"] = param.minimum
            if param.maximum is not None:
                arguments["param_max"] = param.maximum
            if param.step is not None:
                arguments["param_step"] = param.step
            await self._call("parameters", "set_param", arguments)
        # Deliberately do not call list_params here. The deterministic backend
        # may set optimizer guesses and ranges, but a blind Agent must never
        # receive the source card's current/default parameter values.

    async def _stage_filters(self) -> None:
        for item in self.config.filters:
            if item.kind == "data":
                await self._call(
                    "filters",
                    "build_filter_by_data",
                    {"data_source_name": item.data_source_name},
                )
                continue
            path = item.template_path or ""
            if item.local_template_path:
                path = await self._upload(
                    "filters", item.local_template_path, item.upload_group
                )
            await self._call(
                "filters", "build_filter_by_template", {"filter_template_path": path}
            )
        await self._call("filters", "list_filters", {})

    async def _stage_views(self) -> None:
        if not self.config.views:
            return
        await self._call("views", "view", {"view_fields": list(self.config.views)})
        if self.dry_run:
            await self._call("views", "get_view_group_error", {})
            return

        timeout = max(1.0, float(os.getenv("VIEW_READY_TIMEOUT_SECONDS", "900")))
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            result = await self._call("views", "get_view_group_error", {})
            if self._has_finite_view_error(result):
                self.state.resolve("initial_view_error", result)
                return
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(
                    f"views did not produce a finite error within {timeout:.0f}s"
                )
            await asyncio.sleep(2.0)

    async def _stage_optimize(self) -> None:
        if not self.config.optimize.enabled:
            return
        if not self.allow_long_jobs and not self.dry_run:
            raise PermissionError(
                "optimization is configured but disabled; pass --allow-long-jobs explicitly"
            )
        source_name = self._model_source_name()
        if not source_name:
            raise RuntimeError("model source is required before optimization")
        result = await self._call(
            "optimize",
            "start_optimize",
            {
                "model_source_name": source_name,
                "param_string": self.config.optimize.param_string,
            },
        )
        if not self.dry_run:
            job_id = self._parse_job_id(result)
            if not job_id:
                raise RuntimeError("start_optimize returned no job ID")
            self.state.resolve("optimize_job_id", job_id)
            timeout = max(
                1.0, float(os.getenv("OPTIMIZE_TIMEOUT_SECONDS", "7200"))
            )
            deadline = asyncio.get_running_loop().time() + timeout
            while True:
                status_result = await self._call(
                    "optimize", "get_job_status", {"job_id": job_id}
                )
                status = self._parse_job_status(status_result)
                if status:
                    self.state.resolve("optimize_job_status", status)
                if status == "DONE":
                    return
                if status in {"FAILED", "CANCELLED"}:
                    raise RuntimeError(f"optimization ended with status {status}")
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError(
                        f"optimization did not finish within {timeout:.0f}s"
                    )
                await asyncio.sleep(5.0)

    async def _stage_qa(self) -> None:
        if not self.config.qa.enabled:
            return
        if not self.allow_long_jobs and not self.dry_run:
            raise PermissionError(
                "QA is configured but disabled; pass --allow-long-jobs explicitly"
            )
        if (
            self.config.optimize.enabled
            and not self.dry_run
            and self.state.resolved("optimize_job_status") != "DONE"
        ):
            raise RuntimeError(
                "optimization is not recorded as DONE; check its job status before QA"
            )
        result = await self._call("qa", "start_qa", {})
        if not self.dry_run:
            job_id = self._parse_job_id(result)
            if not job_id:
                raise RuntimeError("start_qa returned no job ID")
            self.state.resolve("qa_job_id", job_id)

    async def _stage_export(self) -> None:
        export = self.config.export
        suite_id = self.state.resolved("model_suite_id")
        if (
            self.config.optimize.enabled
            and not self.dry_run
            and self.state.resolved("optimize_job_status") != "DONE"
        ):
            raise RuntimeError(
                "optimization is not recorded as DONE; do not export an unfinished model"
            )
        if export.model_path:
            if suite_id is None:
                raise RuntimeError("model_suite_id is required before save_model")
            await self._call(
                "export",
                "save_model",
                {"model_suite_id": suite_id, "path": export.model_path},
            )
        if export.view_group_path:
            await self._call(
                "export", "dump_view_group", {"path": export.view_group_path}
            )
        if export.report_path:
            if (
                self.state.resolved("qa_job_status") != "DONE"
                and not self.dry_run
            ):
                raise RuntimeError(
                    "QA is not recorded as DONE; check job status before dumping report"
                )
            await self._call(
                "export", "dump_report", {"path": export.report_path}
            )
        if export.save_project:
            try:
                await self._call("export", "save_current_project", {})
            except Exception as exc:
                # Some MeQLab builds throw a viewGroups null-pointer after an
                # otherwise valid optimization. The fitted model and curve
                # export above are the durable results, so record this product
                # defect without discarding them.
                self.state.resolve("save_project_warning", str(exc))

    def _model_source_name(self) -> str | None:
        return self.state.resolved("model_source_name") or (
            self.config.model.source_name if self.config.model else None
        )

    @staticmethod
    def _parse_suite_id(text: str) -> int | None:
        match = re.search(r"(?:ID|id)\s*[:：]\s*(\d+)", text)
        return int(match.group(1)) if match else None

    @staticmethod
    def _parse_source_name(text: str) -> str | None:
        match = re.search(r"(?:名称|name)\s*[:：]\s*([^,，\s]+)", text, re.I)
        return match.group(1).strip() if match else None

    @staticmethod
    def _parse_job_id(text: str) -> str | None:
        match = re.search(
            r"(?:Job\s*ID|job_id)\s*[:：]\s*([0-9a-fA-F-]{16,})", text, re.I
        )
        return match.group(1) if match else None

    @staticmethod
    def _parse_job_status(text: str) -> str | None:
        match = re.search(
            r"(?:Job\s*状态|Job\s*status|job_status)\s*[:：]\s*"
            r"(?:JOB_)?(RUNNING|DONE|FAILED|CANCELLED|[0-3])",
            text,
            re.I,
        )
        if not match:
            return None
        value = match.group(1).upper()
        return {
            "0": "RUNNING",
            "1": "DONE",
            "2": "FAILED",
            "3": "CANCELLED",
        }.get(value, value)

    @staticmethod
    def _has_finite_view_error(text: str) -> bool:
        if re.search(r"(?:共|count\s*[:=]?)\s*0\s*(?:个)?\s*(?:视图|views?)", text, re.I):
            return False
        match = re.search(
            r"(?:聚合误差|aggregate\s+error)\s*[:：=]\s*"
            r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)",
            text,
            re.I,
        )
        if not match:
            return False
        return math.isfinite(float(match.group(1)))


def select_stages(
    *,
    stage: str | None = None,
    from_stage: str | None = None,
    through_stage: str | None = None,
) -> tuple[str, ...]:
    for value in (stage, from_stage, through_stage):
        if value is not None and value not in PIPELINE_STAGES:
            raise ValueError(f"unknown stage: {value}")
    if stage:
        return (stage,)
    start = PIPELINE_STAGES.index(from_stage) if from_stage else 0
    end = PIPELINE_STAGES.index(through_stage) + 1 if through_stage else len(
        PIPELINE_STAGES
    )
    if start >= end:
        raise ValueError("from_stage must not be after through_stage")
    return PIPELINE_STAGES[start:end]
