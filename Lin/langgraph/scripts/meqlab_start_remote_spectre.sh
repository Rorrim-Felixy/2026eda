#!/usr/bin/env bash
set -e
export PRIMARIUS_LICENSE_FILE='15281@121.199.160.165'
export PATH="$HOME/bin:$PATH"
exec "$HOME/AI-Agent/MS-MeQLab/bin/MS-MeQLab" "$@"
