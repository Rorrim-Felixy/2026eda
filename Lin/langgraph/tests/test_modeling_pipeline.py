from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from eda_agent.modeling_pipeline import (
    ModelingPipeline,
    PipelineConfig,
    PipelineState,
    select_stages,
)


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        self.calls.append((name, arguments))
        responses = {
            "list_available_models": "可用模型列表:\n  - ID: 3, 路径：model.lib",
            "add_model_source": "模型源添加成功:\n  - 名称：asm_tt_N，模拟器：Nano",
            "list_model_sources": "模型源列表:\n  - 名称：asm_tt_N，模拟器：Nano",
            "start_optimize": "任务已启动\nJob ID: 550e8400-e29b-41d4-a716-446655440000",
            "start_qa": "任务已启动\nJob ID: 550e8400-e29b-41d4-a716-446655440001",
            "get_job_status": "Job 状态: DONE\n进度: 100%",
        }
        return responses.get(name, "成功")

    async def upload_file(
        self, path: Path, *, group_name: str | None = None
    ) -> str:
        self.calls.append(("upload_file", {"path": str(path), "group": group_name}))
        return "文件上传成功"


def _config(**overrides: Any) -> PipelineConfig:
    raw: dict[str, Any] = {
        "project_name": "unit_project",
        "create_project": False,
        "data_sources": [
            {"name": "iv", "path": "data/iv.pms", "extract_spec": True}
        ],
        "model": {
            "path": "model/model.lib",
            "model_name": "asmhemt101_6",
            "simulator": "Nano"
        },
        "parameters": [
            {
                "name": "voff",
                "value": "-2.0",
                "minimum": -5.0,
                "maximum": 1.0,
                "step": 0.05
            }
        ],
        "filters": [{"kind": "data", "data_source_name": "iv"}],
        "views": [
            {
                "filter_string": "name,iv",
                "source_string": "iv, asm_tt_N",
                "page_string": "id_vgs",
                "selection_string": "xr(0,1)"
            }
        ],
        "export": {"save_project": True}
    }
    raw.update(overrides)
    return PipelineConfig.from_dict(raw)


class ModelingPipelineTests(unittest.TestCase):
    def test_dry_run_records_plan_without_client(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            state = PipelineState(Path(folder) / "state.json", _config())
            pipeline = ModelingPipeline(_config(), state, dry_run=True)
            asyncio.run(pipeline.run(select_stages(through_stage="views")))
            saved = json.loads(state.path.read_text(encoding="utf-8"))
            self.assertEqual(saved["stages"]["views"]["status"], "planned")
            self.assertTrue(saved["actions"])

    def test_live_stage_sequence_and_resolution(self) -> None:
        config = _config()
        client = FakeClient()
        with tempfile.TemporaryDirectory() as folder:
            state = PipelineState(Path(folder) / "state.json", config)
            pipeline = ModelingPipeline(config, state, client, dry_run=False)
            asyncio.run(pipeline.run(select_stages(through_stage="views")))
            names = [name for name, _ in client.calls]
            self.assertLess(names.index("open_project"), names.index("load_data"))
            self.assertLess(names.index("load_data"), names.index("load_model"))
            self.assertLess(names.index("load_model"), names.index("set_param"))
            self.assertLess(names.index("set_param"), names.index("view"))
            self.assertEqual(state.resolved("model_suite_id"), 3)
            self.assertEqual(state.resolved("model_source_name"), "asm_tt_N")

    def test_long_job_requires_second_explicit_switch(self) -> None:
        config = _config(
            optimize={"enabled": True, "param_string": "voff,u0"}
        )
        client = FakeClient()
        with tempfile.TemporaryDirectory() as folder:
            state = PipelineState(Path(folder) / "state.json", config)
            pipeline = ModelingPipeline(config, state, client, dry_run=False)
            with self.assertRaises(PermissionError):
                asyncio.run(pipeline.run(("optimize",)))
            self.assertNotIn("start_optimize", [name for name, _ in client.calls])

    def test_stage_selection(self) -> None:
        self.assertEqual(
            select_stages(from_stage="model", through_stage="filters"),
            ("model", "parameters", "filters"),
        )

    def test_one_shot_job_status_is_recorded(self) -> None:
        config = _config()
        client = FakeClient()
        with tempfile.TemporaryDirectory() as folder:
            state = PipelineState(Path(folder) / "state.json", config)
            state.resolve("optimize_job_id", "550e8400-e29b-41d4-a716-446655440000")
            pipeline = ModelingPipeline(config, state, client, dry_run=False)
            status = asyncio.run(pipeline.refresh_job_status("optimize"))
            self.assertEqual(status, "DONE")
            self.assertEqual(state.resolved("optimize_job_status"), "DONE")


if __name__ == "__main__":
    unittest.main()
