#!/usr/bin/env bash
for archive in "$HOME"/AI-Agent/MS-MeQLab/MS-MeQLab/modules/com-platformda-*.jar; do
    if zipgrep -i -q "privatekey" "$archive" 2>/dev/null ||
       zipgrep -i -q "private_key" "$archive" 2>/dev/null ||
       zipgrep -i -q "identityfile" "$archive" 2>/dev/null ||
       zipgrep -i -q "keyfile" "$archive" 2>/dev/null; then
        echo "$archive"
    fi
done
