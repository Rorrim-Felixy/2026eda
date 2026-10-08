#!/usr/bin/env bash
set -euo pipefail

SPECTRE_BIN="${SPECTRE_BIN:-/opt/cadence/SPECTRE231/bin/spectre}"

"$SPECTRE_BIN" -64 -format psfascii \
    -raw truth_all.raw \
    +log truth_all.log \
    truth_all.scs
