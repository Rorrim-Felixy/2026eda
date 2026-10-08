#!/usr/bin/env bash
for archive in "$HOME"/AI-Agent/MS-MeQLab/MS-MeQLab/modules/*.jar; do
    if zipgrep -i -q "remote spectre" "$archive" 2>/dev/null; then
        echo "$archive"
    fi
done
