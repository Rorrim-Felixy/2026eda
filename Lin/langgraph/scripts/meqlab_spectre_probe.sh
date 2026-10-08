#!/usr/bin/env bash
{
    date -Is
    printf 'cwd=%s\n' "$PWD"
    printf 'argc=%s\n' "$#"
    index=0
    for argument in "$@"; do
        printf 'argv[%d]=%q\n' "$index" "$argument"
        index=$((index + 1))
    done
    for argument in "$@"; do
        case "$argument" in
            *.sp|*.scs)
                if [[ -f "$argument" ]]; then
                    mkdir -p /tmp/meqlab_spectre_probe_files
                    cp -f -- "$argument" /tmp/meqlab_spectre_probe_files/
                    printf '%s\n' "--- netlist: $argument ---"
                    sed -n '1,260p' -- "$argument"
                    printf '%s\n' '--- end netlist ---'
                fi
                ;;
        esac
    done
    printf '%s\n' '--- environment ---'
    env | sort
    printf '%s\n' '--- end ---'
} >> /tmp/meqlab_spectre_probe.log 2>&1
exit 0
