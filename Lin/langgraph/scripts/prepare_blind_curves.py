from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path

from eda_agent.config import PROJECT_DIR


ALLOWED_COLUMNS = (
    "dataset_type",
    "temperature_c",
    "vgs_v",
    "vds_v",
    "id_a",
    "ig_a",
)


def _f(row: dict[str, str], name: str) -> float:
    return float(row[name])


def _nearest(values: list[float], target: float) -> float:
    return min(values, key=lambda value: abs(value - target))


def _curve_stats(rows: list[dict[str, str]]) -> dict[str, float | int]:
    currents = [abs(_f(row, "id_a")) for row in rows]
    nonzero = [value for value in currents if value > 0.0]
    floor = min(nonzero) if nonzero else 0.0
    ceiling = max(currents) if currents else 0.0
    decades = math.log10(ceiling / floor) if floor > 0.0 and ceiling > floor else 0.0
    return {
        "points": len(rows),
        "max_abs_id_a": ceiling,
        "current_dynamic_range_decades": decades,
    }


def _write_pms(
    destination: Path,
    selected: list[tuple[str, list[dict[str, str]]]],
) -> None:
    lines = [
        "// Primarius Technologies Co., Ltd.",
        "// Blind voltage/current subset selected from Cadence sweep data.",
        "// Source model parameters are intentionally absent.",
        "",
    ]
    for group_name, rows in selected:
        first = rows[0]
        temp = _f(first, "temperature_c")
        if first["dataset_type"] == "id_vg":
            page_name = "Id_Vg"
            by_x: dict[float, dict[float, float]] = defaultdict(dict)
            for row in rows:
                by_x[_f(row, "vgs_v")][_f(row, "vds_v")] = _f(row, "id_a")
            p_values = sorted({_f(row, "vds_v") for row in rows})
            p_text = ",".join(format(value, ".12g") for value in p_values)
            lines.append(
                "{group="
                + page_name
                + ",y=(Id),x=Vgs,p=Vds("
                + p_text
                + "),condition=(ref_vs=0,vbs=0),"
                + f"device=(type=Mosfet,polarity=NMOS,w=200.0,t={temp:g},l=0.25,nf=1)}}"
            )
            for x in sorted(by_x):
                values = "\t".join(format(by_x[x][p], ".15g") for p in p_values)
                lines.append(f"{x:.15g}\t{values}")
        else:
            page_name = "Id_Vd"
            vgs = _f(first, "vgs_v")
            lines.append(
                "{group="
                + page_name
                + ",y=(Id),x=Vds,p=Vgs("
                + format(vgs, ".12g")
                + "),condition=(ref_vs=0,vbs=0),"
                + f"device=(type=Mosfet,polarity=NMOS,w=200.0,t={temp:g},l=0.25,nf=1)}}"
            )
            for row in sorted(rows, key=lambda item: _f(item, "vds_v")):
                lines.append(
                    f"{_f(row, 'vds_v'):.15g}\t{_f(row, 'id_a'):.15g}"
                )
        lines.append("")
    destination.write_text("\n".join(lines), encoding="utf-8")


def prepare(source: Path, output_dir: Path) -> dict[str, object]:
    with source.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    missing = set(ALLOWED_COLUMNS) - set(rows[0] if rows else {})
    if missing:
        raise ValueError(f"missing required voltage/current columns: {sorted(missing)}")

    temperatures = sorted({_f(row, "temperature_c") for row in rows})
    room = _nearest(temperatures, 27.0)
    cold, hot = temperatures[0], temperatures[-1]
    idvg = [row for row in rows if row["dataset_type"] == "id_vg"]
    idvd = [row for row in rows if row["dataset_type"] == "id_vd"]
    vds_values = sorted({_f(row, "vds_v") for row in idvg})
    vgs_values = sorted({_f(row, "vgs_v") for row in idvd})
    active_vgs = [value for value in vgs_values if value >= 0.0]
    middle_vgs = _nearest(active_vgs or vgs_values, 0.0)
    high_vgs = max(vgs_values)

    definitions = [
        (
            "Id_Vg_linear_room",
            [
                row
                for row in idvg
                if _f(row, "temperature_c") == room
                and _f(row, "vds_v") == min(vds_values)
            ],
            "linear-region transfer curve",
        ),
        (
            "Id_Vg_highVd_room",
            [
                row
                for row in idvg
                if _f(row, "temperature_c") == room
                and _f(row, "vds_v") == max(vds_values)
            ],
            "high-drain-bias transfer curve",
        ),
        (
            "Id_Vd_cold_highVg",
            [
                row
                for row in idvd
                if _f(row, "temperature_c") == cold
                and _f(row, "vgs_v") == high_vgs
            ],
            "cold high-current output envelope",
        ),
        (
            "Id_Vd_room_midVg",
            [
                row
                for row in idvd
                if _f(row, "temperature_c") == room
                and _f(row, "vgs_v") == middle_vgs
            ],
            "room-temperature mid-current output curve",
        ),
        (
            "Id_Vd_hot_highVg",
            [
                row
                for row in idvd
                if _f(row, "temperature_c") == hot
                and _f(row, "vgs_v") == high_vgs
            ],
            "hot high-current output envelope",
        ),
    ]
    if any(not curve_rows for _, curve_rows, _ in definitions):
        raise RuntimeError("one or more selected curve groups are empty")

    output_dir.mkdir(parents=True, exist_ok=True)
    selected_rows = [row for _, curve_rows, _ in definitions for row in curve_rows]
    csv_path = output_dir / "blind_selected_curves.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=ALLOWED_COLUMNS)
        writer.writeheader()
        for row in selected_rows:
            writer.writerow({name: row[name] for name in ALLOWED_COLUMNS})

    # MeQLab recognizes the canonical Id_Vg/Id_Vd page names. Combine both
    # room-temperature transfer biases into one page, and keep one Id_Vd block
    # per temperature just like the original Cadence PMS export.
    pms_selected = [
        ("Id_Vg", definitions[0][1] + definitions[1][1]),
        ("Id_Vd", definitions[2][1]),
        ("Id_Vd", definitions[3][1]),
        ("Id_Vd", definitions[4][1]),
    ]
    pms_path = output_dir / "blind_selected_curves.pms"
    _write_pms(pms_path, pms_selected)

    manifest: dict[str, object] = {
        "blind_input_contract": {
            "allowed": list(ALLOWED_COLUMNS),
            "forbidden": [
                "source model card",
                "generation parameter values",
                "PMMS get_param/list_params results",
            ],
        },
        "selection_method": (
            "bias/temperature envelope selection using only voltage, current, "
            "temperature, and sweep type"
        ),
        "source_row_count": len(rows),
        "selected_row_count": len(selected_rows),
        "groups": [
            {
                "name": name,
                "purpose": purpose,
                "temperature_c": _f(curve_rows[0], "temperature_c"),
                "vgs_range_v": [
                    min(_f(row, "vgs_v") for row in curve_rows),
                    max(_f(row, "vgs_v") for row in curve_rows),
                ],
                "vds_range_v": [
                    min(_f(row, "vds_v") for row in curve_rows),
                    max(_f(row, "vds_v") for row in curve_rows),
                ],
                **_curve_stats(curve_rows),
            }
            for name, curve_rows, purpose in definitions
        ],
        "outputs": {"csv": csv_path.name, "pms": pms_path.name},
    }
    manifest_path = output_dir / "blind_selection_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Select five diverse ASM-HEMT curves without reading model parameters."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=(
            PROJECT_DIR
            / "artifacts"
            / "asmhemt101_6"
            / "cadence_ic618"
            / "asmhemt_all_dc.csv"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_DIR / "artifacts" / "asmhemt101_6" / "blind",
    )
    args = parser.parse_args()
    manifest = prepare(args.source.resolve(), args.output_dir.resolve())
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
