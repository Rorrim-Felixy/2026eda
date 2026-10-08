from __future__ import annotations

from pathlib import Path


def _frange(start: float, stop: float, step: float) -> list[float]:
    count = int(round((stop - start) / step))
    return [round(start + index * step, 12) for index in range(count + 1)]


def _page(
    *,
    group: str,
    x_name: str,
    x_values: list[float],
    p_name: str,
    p_values: list[float],
    conditions: dict[str, float],
    temperature: float,
) -> str:
    p_list = ",".join(f"{value:g}" for value in p_values)
    condition_text = ",".join(f"{key}={value:g}" for key, value in conditions.items())
    header = (
        f"{{group={group},y=(Id),x={x_name},p={p_name}({p_list}),"
        f"condition=({condition_text}),"
        "device=(type=Mosfet,polarity=NMOS,w=200.0,"
        f"t={temperature:g},l=0.25,nf=1)}}"
    )
    rows = [header]
    # MeQLab drops a sweep page when every dependent value is exactly zero.
    # A tiny finite seed preserves the requested bias grid; save_sim_result then
    # replaces these seeds with currents from the registered Nano model.
    placeholder = "\t".join("1e-30" for _ in p_values)
    rows.extend(f"{x:g}\t{placeholder}" for x in x_values)
    return "\n".join(rows)


def write_dc_bias_template(destination: Path) -> None:
    """Write sweep coordinates that MeQLab can use to simulate ASM-HEMT.

    Zero-valued current columns are placeholders only. `save_sim_result` replaces
    them with Nano simulation results from the registered model source.
    """
    header_lines = [
        "// Primarius Technologies Co., Ltd.",
        "// Sweep Data",
        "// Synthetic bias grid; 1e-30 values are placeholders, not measurements.",
    ]
    pages: list[str] = []
    for temperature in (-40.0, 25.0, 125.0):
        pages.append(
            _page(
                group="Id_Vg",
                x_name="Vgs",
                x_values=_frange(-4.0, 2.0, 0.1),
                p_name="Vds",
                p_values=[0.1, 1.0, 5.0, 10.0, 20.0],
                conditions={"ref_vs": 0.0, "vbs": 0.0},
                temperature=temperature,
            )
        )
        pages.append(
            _page(
                group="id_vd",
                x_name="Vds",
                x_values=_frange(0.0, 20.0, 0.25),
                p_name="Vgs",
                p_values=[-2.0, -1.0, 0.0, 1.0, 2.0],
                conditions={"ref_vs": 0.0, "vbs": 0.0},
                temperature=temperature,
            )
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(header_lines) + "\n" + "\n\n".join(pages) + "\n"
    destination.write_text(text, encoding="utf-8")
