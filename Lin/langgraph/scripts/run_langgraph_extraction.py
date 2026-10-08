from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

from eda_agent.blind import filter_tool_specs
from eda_agent.config import PROJECT_DIR, Settings
from eda_agent.graph import build_graph
from eda_agent.modeling_pipeline import ModelingPipeline, PipelineConfig, PipelineState
from eda_agent.models import OpenAIDualModelService
from eda_agent.tools import PMMSClient


AGENT_TOOLS = {"start_optimize", "get_job_status", "get_view_group_error"}


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare MeQLab, then let the dual-model LangGraph agent run extraction."
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--agent-result", type=Path, required=True)
    return parser.parse_args()


def _latest_job_done(history: list[dict[str, Any]]) -> tuple[str | None, bool]:
    latest_id: str | None = None
    latest_index = -1
    for index, item in enumerate(history):
        if item.get("role") != "tool" or item.get("tool") != "start_optimize":
            continue
        match = re.search(r"Job ID:\s*([0-9a-f-]+)", str(item.get("result", "")))
        if match:
            latest_id = match.group(1)
            latest_index = index
    if latest_id is None:
        return None, False
    done = any(
        index > latest_index
        and item.get("role") == "tool"
        and item.get("tool") == "get_job_status"
        and item.get("arguments", {}).get("job_id") == latest_id
        and "Job 状态: DONE" in str(item.get("result", ""))
        for index, item in enumerate(history)
    )
    return latest_id, done


async def _run(args: argparse.Namespace) -> None:
    config = PipelineConfig.load(args.config.resolve())
    state = PipelineState(args.state.resolve(), config)
    settings = Settings.load(require_models=True)
    models = OpenAIDualModelService(settings)

    async with PMMSClient(settings.pmms_exe, settings.pmms_config) as client:
        pipeline = ModelingPipeline(config, state, client, dry_run=False)
        await pipeline.run(
            ("project", "data", "model", "parameters", "filters", "views")
        )
        initial_error = str(state.resolved("initial_view_error", ""))
        safe_specs = [
            spec
            for spec in filter_tool_specs(client.tool_specs)
            if spec.get("name") in AGENT_TOOLS
        ]
        graph = build_graph(
            models,
            client,
            approval_score=settings.approval_score,
            blind_mode=True,
        )
        task = (
            "你是本次反向参数提取的执行 Agent。MeQLab 工程、盲初值模型、两组 DC 曲线、"
            "参数范围、filter 和 view 已在当前会话准备完成。你必须亲自调用可用工具完成提取，"
            "不能只写计划。本次只允许调用一次 start_optimize，严禁启动第二个优化任务。"
            "先调用 start_optimize，model_source_name=asmhemt_blind_S，"
            "param_string=voff,nfactor,u0,vsat,lambda；从返回值取得 job_id，然后反复调用 "
            "get_job_status，只有看到 DONE 才能停止轮询；最后调用 get_view_group_error 取得"
            "拟合后的有限误差并对比初始误差，随后立即给出结论并交由评审，绝对不要再次调用 "
            "start_optimize。若仍为 RUNNING 或 nan，必须继续查询，不能宣称完成。"
            "不要请求隐藏真值参数或模型卡。初始曲线误差如下：\n" + initial_error
        )
        result = await graph.ainvoke(
            {
                "task": task,
                "iteration": 0,
                "review_round": 0,
                "max_review_rounds": settings.max_review_rounds,
                "tool_call_count": 0,
                "max_tool_calls": settings.max_tool_calls,
                "available_tools": safe_specs,
                "history": [],
                "critique": "",
                "tool_result": "",
                "error": "",
            }
        )
        result["model_call_trace"] = models.call_trace
        result["initial_view_error"] = initial_error
        history = list(result.get("history", []))
        job_id, latest_done = _latest_job_done(history)
        result["optimize_job_id"] = job_id
        result["latest_optimize_done"] = latest_done
        target = args.agent_result.resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        if not latest_done:
            raise RuntimeError("LangGraph Agent did not observe its latest optimization job as DONE")

        final_error = await client.call_tool("get_view_group_error", {})
        state.resolve("optimize_job_status", "DONE")
        state.resolve("final_view_error", final_error)
        await pipeline.run(("export",))

        fitted: dict[str, str] = {}
        fitted_model_source = str(state.resolved("model_source_name", ""))
        for parameter in config.parameters:
            fitted[parameter.name] = await client.call_tool(
                "get_param",
                {
                    "model_source_name": fitted_model_source,
                    "param_name": parameter.name,
                },
            )
        result["final_view_error"] = final_error
        result["fitted_parameters"] = fitted

    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"Agent result: {target}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(_run(_arguments()))
