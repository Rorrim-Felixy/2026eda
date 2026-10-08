from __future__ import annotations

import argparse
import csv
import json
import math
import re
import tarfile
from pathlib import Path
from typing import Any


NUMBER_RE = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")


def _read_member(archive: Path, member: str) -> str:
    with tarfile.open(archive, "r:gz") as handle:
        return handle.extractfile(member).read().decode("utf-8")  # type: ignore[union-attr]


def _parse_dc_values(text: str, x_name: str) -> list[dict[str, float]]:
    lines = text.splitlines()
    try:
        start = lines.index("VALUE") + 1
        end = lines.index("END", start)
    except ValueError as exc:
        raise ValueError("PSF file has no VALUE/END section") from exc

    records: list[dict[str, float]] = []
    current: dict[str, float] = {}
    for line in lines[start:end]:
        match = re.match(r'^"([^"]+)"\s+(' + NUMBER_RE.pattern + r")\s*$", line)
        if not match:
            continue
        name, raw_value = match.groups()
        if name == x_name and current:
            records.append(current)
            current = {}
        current[name] = float(raw_value)
    if current:
        records.append(current)
    return records


def _write_pms(path: Path, vg: list[dict[str, float]], vd: list[dict[str, float]]) -> None:
    lines = [
        "// Primarius Technologies Co., Ltd.",
        "// Sweep Data",
        "// Generated from Cadence Spectre ASM-HEMT 101.6.0 simulation.",
        "",
        "{group=Id_Vg,y=(Id),x=Vgs,p=Vds(5),condition=(ref_vs=0,vbs=0),"
        "device=(type=Mosfet,polarity=NMOS,w=200.0,t=27,l=0.25,nf=1)}",
    ]
    for row in sorted(vg, key=lambda item: item["vgs_v"]):
        lines.append(f"{row['vgs_v']:.15g}\t{row['id_a']:.15g}")
    lines.extend(
        [
            "",
            "{group=id_vd,y=(Id),x=Vds,p=Vgs(0),condition=(ref_vs=0,vbs=0),"
            "device=(type=Mosfet,polarity=NMOS,w=200.0,t=27,l=0.25,nf=1)}",
        ]
    )
    for row in sorted(vd, key=lambda item: item["vds_v"]):
        lines.append(f"{row['vds_v']:.15g}\t{row['id_a']:.15g}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def parse(archive: Path, output_dir: Path) -> dict[str, Any]:
    vg_raw = _parse_dc_values(_read_member(archive, "truth_all.raw/dcVg.dc"), "VGS")
    vd_raw = _parse_dc_values(_read_member(archive, "truth_all.raw/dcVd.dc"), "VDS")
    if not vg_raw or not vd_raw:
        raise RuntimeError("one or both DC sweeps are empty")

    vg = [
        {
            "dataset_type": "id_vg",
            "temperature_c": 27.0,
            "vgs_v": row["VGS"],
            "vds_v": 5.0,
            "id_a": -row["Pd:p"],
            "ig_a": -row.get("Pg:p", 0.0),
        }
        for row in vg_raw
    ]
    vd = [
        {
            "dataset_type": "id_vd",
            "temperature_c": 27.0,
            "vgs_v": 0.0,
            "vds_v": row["VDS"],
            "id_a": -row["Pd:p"],
            "ig_a": -row.get("Pg:p", 0.0),
        }
        for row in vd_raw
    ]
    for rows in (vg, vd):
        for row in rows:
            for key in ("vgs_v", "vds_v", "id_a", "ig_a"):
                if not math.isfinite(row[key]):
                    raise ValueError(f"non-finite value in {row}")

    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "truth_all_dc.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = (
            "dataset_type",
            "temperature_c",
            "vgs_v",
            "vds_v",
            "id_a",
            "ig_a",
        )
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(vg)
        writer.writerows(vd)

    pms_path = output_dir / "truth_all_dc.pms"
    _write_pms(pms_path, vg, vd)
    manifest = {
        "source_archive": archive.name,
        "truth_model": "ASM-HEMT 101.6.0 defaults + shmod=0 + trapmod=1",
        "blind_contract": {
            "allowed_columns": list(fieldnames),
            "forbidden": ["model card", "truth parameter values", "list_params/get_param"],
        },
        "curves": {
            "Id_Vg": {
                "points": len(vg),
                "vds_v": 5.0,
                "id_abs_min_a": min(abs(row["id_a"]) for row in vg),
                "id_abs_max_a": max(abs(row["id_a"]) for row in vg),
            },
            "Id_Vd": {
                "points": len(vd),
                "vgs_v": 0.0,
                "id_abs_min_a": min(abs(row["id_a"]) for row in vd),
                "id_abs_max_a": max(abs(row["id_a"]) for row in vd),
            },
        },
        "outputs": {"csv": csv_path.name, "pms": pms_path.name},
    }
    manifest_path = output_dir / "truth_all_dc_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            parse(args.archive.resolve(), args.output_dir.resolve()),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
