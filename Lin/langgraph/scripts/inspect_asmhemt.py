from __future__ import annotations

import argparse
import json
from pathlib import Path

from eda_agent.asmhemt import inspect_asmhemt, write_hspice_wrapper, write_manifest


DEFAULT_SOURCE = Path(
    r"C:\AI Agent\ASM-HEMT\ASM-HEMT101.6.0_05132026\vacode\asmhemt.va"
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect the official ASM-HEMT Verilog-A source")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    info = inspect_asmhemt(args.source)
    summary = {
        "module": info.module,
        "version": info.version,
        "ports": info.ports,
        "parameter_count": len(info.parameters),
        "model_parameter_count": sum(p.scope == "model" for p in info.parameters),
        "instance_parameter_count": sum(p.scope == "instance" for p in info.parameters),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if args.output_dir:
        output_dir = args.output_dir.resolve()
        write_manifest(info, output_dir / "asmhemt101_6_manifest.json")
        write_hspice_wrapper(info, output_dir / "asmhemt101_6.lib")
        print(f"Generated: {output_dir}")


if __name__ == "__main__":
    main()
