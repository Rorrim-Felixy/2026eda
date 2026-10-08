from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import zipfile
from pathlib import Path

from eda_agent.asmhemt import (
    inspect_asmhemt,
    write_hspice_wrapper,
    write_manifest,
    write_nano_isothermal_source,
)
from eda_agent.config import PROJECT_DIR, Settings
from eda_agent.synthetic_data import write_dc_bias_template
from eda_agent.tools import PMMSClient


DEFAULT_SOURCE = Path(
    r"C:\AI Agent\ASM-HEMT\ASM-HEMT101.6.0_05132026\vacode\asmhemt.va"
)
DEFAULT_PROJECT = "ASMHEMT101_6_Modeling"
DEFAULT_GROUP = "asmhemt101_6"


def _contains_yes(text: str) -> bool:
    lowered = text.lower()
    return "是" in text or "yes" in lowered or "true" in lowered


def _first_model_id(text: str) -> int | None:
    match = re.search(r"(?:ID|id)\s*[:：]\s*(\d+)", text)
    return int(match.group(1)) if match else None


def _data_source_names(text: str) -> list[str]:
    return re.findall(r"^\s*-\s*([^\s(]+)\s*\(ID\s*[:：]", text, re.M | re.I)


def _require_success(step: str, text: str) -> str:
    lowered = text.lower()
    if "错误" in text or "error" in lowered or "失败" in text:
        raise RuntimeError(f"{step} failed: {text}")
    return text


def _summarize_sim_archive(path: Path) -> dict[str, int]:
    summary = {
        "files": 0,
        "values": 0,
        "finite_values": 0,
        "nonzero_values": 0,
        "nan_values": 0,
    }
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.lower().endswith(".dat"):
                continue
            summary["files"] += 1
            text = archive.read(name).decode("utf-8", errors="replace")
            for line in text.splitlines():
                if not line or line[0] in "{[" or "," not in line:
                    continue
                for item in line.split(",")[1:]:
                    try:
                        value = float(item)
                    except ValueError:
                        continue
                    summary["values"] += 1
                    if math.isfinite(value):
                        summary["finite_values"] += 1
                        if value != 0.0:
                            summary["nonzero_values"] += 1
                    else:
                        summary["nan_values"] += 1
    return summary


async def run(args: argparse.Namespace) -> None:
    settings = Settings.load(require_models=False)
    info = inspect_asmhemt(args.source)
    build_dir = (PROJECT_DIR / "artifacts" / "asmhemt101_6").resolve()
    build_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = build_dir / "asmhemt101_6_manifest.json"
    wrapper_path = build_dir / "asmhemt101_6.lib"
    nano_source_path = build_dir / "asmhemt_nano_isothermal.va"
    bias_template_path = build_dir / "asmhemt101_6_dc_bias_template.pms"
    write_manifest(info, manifest_path)
    write_nano_isothermal_source(args.source, nano_source_path)
    write_hspice_wrapper(
        info, wrapper_path, hdl_filename=nano_source_path.name
    )
    write_dc_bias_template(bias_template_path)

    report: dict[str, object] = {
        "source": str(args.source.resolve()),
        "module": info.module,
        "version": info.version,
        "ports": info.ports,
        "parameter_count": len(info.parameters),
        "project": args.project,
        "steps": [],
    }
    steps: list[dict[str, object]] = report["steps"]  # type: ignore[assignment]

    async with PMMSClient(settings.pmms_exe, settings.pmms_config) as pmms:
        exists = await pmms.call_tool("exist_project", {"project_name": args.project})
        steps.append({"tool": "exist_project", "result": exists})
        if _contains_yes(exists):
            opened = await pmms.call_tool("open_project", {"project_name": args.project})
            steps.append({"tool": "open_project", "result": opened})
        else:
            created = await pmms.call_tool(
                "generate_project",
                {
                    "project_name": args.project,
                    "device_type": "Mosfet",
                    "device_polarity": "NMOS",
                },
            )
            steps.append({"tool": "generate_project", "result": created})

        for path in (
            args.source,
            nano_source_path,
            manifest_path,
            wrapper_path,
            bias_template_path,
        ):
            uploaded = await pmms.upload_file(path, group_name=args.group)
            _require_success(f"upload_file({path.name})", uploaded)
            steps.append({"tool": "upload_file", "file": path.name, "result": uploaded})

        wrapper_remote = f"{args.group}/{wrapper_path.name}"
        loaded = await pmms.call_tool(
            "load_model", {"path": wrapper_remote, "format": "hspice"}
        )
        _require_success("load_model", loaded)
        steps.append({"tool": "load_model", "path": wrapper_remote, "result": loaded})

        available = await pmms.call_tool("list_available_models", {})
        steps.append({"tool": "list_available_models", "result": available})
        model_id = _first_model_id(available)
        report["model_suite_id"] = model_id

        if model_id is not None and args.add_source:
            added = await pmms.call_tool(
                "add_model_source",
                {
                    "model_suite_id": model_id,
                    "model_name": "asmhemt101_6",
                    "simulator": "Nano",
                },
            )
            _require_success("add_model_source", added)
            steps.append({"tool": "add_model_source", "result": added})
            sources = await pmms.call_tool("list_model_sources", {})
            steps.append({"tool": "list_model_sources", "result": sources})

            if args.simulate:
                data_name = "asmhemt101_6_bias_template"
                loaded_data = await pmms.call_tool(
                    "load_data",
                    {
                        "data_type": 0,
                        "path": f"{args.group}/{bias_template_path.name}",
                        "data_source_name": data_name,
                        "device_type": "Mosfet",
                        "device_polarity": "NMOS",
                    },
                )
                _require_success("load_data", loaded_data)
                steps.append({"tool": "load_data", "result": loaded_data})

                data_sources = await pmms.call_tool("list_data_sources", {})
                steps.append({"tool": "list_data_sources", "result": data_sources})
                source_names = _data_source_names(data_sources)
                if data_name in source_names:
                    actual_data_name = data_name
                elif source_names:
                    actual_data_name = source_names[-1]
                else:
                    raise RuntimeError(
                        "MeQLab accepted load_data but registered no measurement source: "
                        + data_sources
                    )
                report["data_source_name"] = actual_data_name

                simulated_remote = f"{args.group}/asmhemt101_6_synthetic_dc.pms"
                simulated = await pmms.call_tool(
                    "save_sim_result",
                    {
                        "data_source_name": actual_data_name,
                        "path": simulated_remote,
                    },
                )
                _require_success("save_sim_result", simulated)
                steps.append({"tool": "save_sim_result", "result": simulated})
                local_simulated = build_dir / "asmhemt101_6_synthetic_dc.pms"
                await pmms.download_file(simulated_remote, local_simulated)
                report["synthetic_data"] = str(local_simulated)
                simulation_summary = _summarize_sim_archive(local_simulated)
                report["simulation_summary"] = simulation_summary
                if (
                    simulation_summary["finite_values"] == 0
                    or simulation_summary["nonzero_values"] == 0
                ):
                    raise RuntimeError(
                        "Nano produced no usable nonzero simulation values: "
                        + json.dumps(simulation_summary)
                    )

        try:
            saved = await pmms.call_tool("save_current_project", {})
        except RuntimeError as error:
            # MeQLab 1.26.1 throws when no view group exists.  The model and
            # downloaded simulation data are already durable, so record it as
            # a non-fatal product defect instead of hiding a successful run.
            saved = f"non-fatal save error: {error}"
        steps.append({"tool": "save_current_project", "result": saved})
        report["pmms_trace_count"] = len(pmms.trace)

    report_path = build_dir / "bootstrap_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Report: {report_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create an ASM-HEMT MeQLab project through PMMS")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--group", default=DEFAULT_GROUP)
    parser.add_argument(
        "--add-source",
        action="store_true",
        help="After parsing the model card, register asmhemt101 with Nano",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Load the generated bias template and save Nano simulation results",
    )
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
