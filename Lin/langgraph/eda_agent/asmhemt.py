from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


MODULE_RE = re.compile(r"\bmodule\s+(?P<name>[A-Za-z_]\w*)\s*\((?P<ports>[^)]*)\)\s*;")
VERSION_RE = re.compile(r"ASM\s+HEMT\s+Model\s+Version\s+(?P<version>[\d.]+)", re.I)
PARAM_RE = re.compile(
    r"^\s*`(?P<macro>(?:MPR|MPI|IPR|IPI)\w*)\(\s*"
    r"(?P<name>[A-Za-z_]\w*)\s*,\s*(?P<default>[^,]+)",
    re.M,
)


@dataclass(frozen=True)
class ASMParameter:
    name: str
    default: str
    macro: str
    scope: str
    value_type: str


@dataclass(frozen=True)
class ASMModelInfo:
    path: str
    module: str
    version: str
    ports: list[str]
    parameters: list[ASMParameter]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def inspect_asmhemt(path: Path) -> ASMModelInfo:
    source = path.resolve()
    text = source.read_text(encoding="utf-8", errors="replace")
    module_match = MODULE_RE.search(text)
    if not module_match:
        raise ValueError(f"No Verilog-A module declaration found in {source}")
    version_match = VERSION_RE.search(text)

    parameters: list[ASMParameter] = []
    seen: set[str] = set()
    for match in PARAM_RE.finditer(text):
        name = match.group("name")
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        macro = match.group("macro")
        parameters.append(
            ASMParameter(
                name=name,
                default=match.group("default").strip(),
                macro=macro,
                scope="model" if macro.startswith("M") else "instance",
                value_type="integer" if macro[1:3] in {"PI", "II"} else "real",
            )
        )

    return ASMModelInfo(
        path=str(source),
        module=module_match.group("name"),
        version=version_match.group("version") if version_match else "unknown",
        ports=[part.strip() for part in module_match.group("ports").split(",")],
        parameters=parameters,
    )


def write_manifest(info: ASMModelInfo, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(info.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_hspice_wrapper(
    info: ASMModelInfo,
    destination: Path,
    *,
    subckt_name: str = "asmhemt101_6",
    hdl_filename: str = "asmhemt.va",
) -> None:
    """Create a Nano/HSPICE-facing subcircuit around the Verilog-A module.

    The wrapper deliberately exposes all Verilog-A parameters on the subcircuit so
    MeQLab can discover and tune them. Actual simulator compatibility is verified
    by the bootstrap script before the file is used for extraction.
    """
    external_ports = info.ports[:4]
    if len(external_ports) != 4:
        raise ValueError(f"ASM-HEMT requires four electrical ports, got {info.ports}")
    # `dt` is a thermal-discipline port, not an electrical node.  Connecting it
    # to SPICE ground can crash Nano.  ASM-HEMT guards it with
    # $port_connected(dt), so omit the optional fifth port for isothermal DC.
    core_ports = external_ports

    lines = [
        "* Auto-generated ASM-HEMT model card for MeQLab/Nano",
        f"* Source module: {info.module}; version: {info.version}",
        "* The optional fifth thermal port is intentionally left unconnected.",
        f'.hdl "{hdl_filename}"',
        "",
        f".subckt {subckt_name} {' '.join(external_ports)}",
    ]
    for parameter in info.parameters:
        lines.append(f"+ {parameter.name}={parameter.default}")
    lines.extend(
        [
            "",
            # Nano's HSPICE parser does not support the HSPICE-specific Y
            # Verilog-A element.  Modules brought in by .hdl are resolved as
            # subcircuits, so use the portable X instance form.
            f"Xcore {' '.join(core_ports)} {info.module}",
        ]
    )
    for parameter in info.parameters:
        lines.append(f"+ {parameter.name}={parameter.name}")
    lines.extend([f".ends {subckt_name}", ""])
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines), encoding="utf-8")


def write_nano_isothermal_source(source: Path, destination: Path) -> None:
    """Create a four-electrical-port ASM-HEMT source for Nano DC runs.

    MeQLab's bundled Nano crashes on the Verilog-A thermal discipline used by
    ASM-HEMT 101.6.0.  This narrowly-scoped derivative disables only the
    self-heating network and keeps the original electrical equations intact.
    The untouched upstream source remains beside the generated derivative.
    """

    text = source.read_text(encoding="utf-8")

    def replace_once(old: str, new: str, label: str) -> None:
        nonlocal text
        count = text.count(old)
        if count != 1:
            raise ValueError(f"Expected one {label} block, found {count}")
        text = text.replace(old, new, 1)

    replace_once(
        "module asmhemt(d,g,s,b,dt);\n    inout d,g,s,b,dt;",
        "module asmhemt(d,g,s,b);\n    inout d,g,s,b;",
        "module port",
    )
    replace_once("    thermal dt;\n", "", "thermal declaration")
    replace_once(
        "////////// Branches Self-heating //////////\n"
        "    branch (dt) rth;\n"
        "    branch (dt) ith;",
        "////////// Self-heating disabled for Nano isothermal compatibility //////////",
        "thermal branch",
    )
    replace_once(
        "            if ($port_connected(dt) == 0) begin\n"
        "                if (shmod == 0 || rth0 == 0.0) begin\n"
        "                    Temp(dt) <+ 0.0;\n"
        "                end else begin\n"
        "                    $strobe(\"5 terminal Module, while 't' node is not connected, SH is activated.\");\n"
        "                end\n"
        "            end\n",
        "",
        "port-connected",
    )
    replace_once(
        "            Tdev = $temperature + Temp(rth) + dtemp;",
        "            Tdev = $temperature + dtemp;",
        "device temperature",
    )
    replace_once(
        "////////// Self-Heating Effect //////////\n"
        "            if (shmod == 1 && rth0>0) begin\n"
        "                Pwr(ith) <+ -1.0*Ids*Vds-1.0*Ids_fp1*Vds_fp1-1.0*Ids_fp2*Vds_fp2-1.0*Ids_fp3*Vds_fp3-1.0*Ids_fp4*Vds_fp4;\n"
        "                Pwr(rth) <+ Temp(rth)/rth0;\n"
        "                Pwr(rth) <+ ddt(Temp(rth)*cth0);\n"
        "            end else begin\n"
        "                Temp(dt) <+ 0.0 ;\n"
        "            end",
        "////////// Self-heating disabled: isothermal Nano run //////////",
        "self-heating contribution",
    )
    replace_once(
        "            t_delta_sh = Temp(dt);",
        "            t_delta_sh = 0.0;",
        "thermal output",
    )

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
