#!/usr/bin/env bash
set -u

bridge_root="${IC_SPECTRE_BRIDGE_ROOT:-/mnt/hgfs/D/codex_truth_bridge}"
spectre_bin="${SPECTRE_BIN:-/opt/cadence/SPECTRE231/bin/spectre}"
mkdir -p "$bridge_root/requests" "$bridge_root/running" "$bridge_root/results"
printf '%s worker started pid=%s spectre=%s\n' "$(date -Is)" "$$" "$spectre_bin"

while true; do
    handled=0
    for ready in "$bridge_root"/requests/*/READY; do
        [[ -f "$ready" ]] || continue
        request_dir="$(dirname "$ready")"
        job_id="$(basename "$request_dir")"
        run_dir="$bridge_root/running/$job_id"
        if ! mv "$request_dir" "$run_dir" 2>/dev/null; then
            continue
        fi
        handled=1
        printf '%s running %s\n' "$(date -Is)" "$job_id"
        (
            cd "$run_dir" || exit 70
            rm -rf job.raw
            "$spectre_bin" -64 =log job.log job.sp -r job.raw
        )
        rc=$?
        printf '%s\n' "$rc" > "$run_dir/exit_code"
        [[ -e "$run_dir/job.raw" ]] || : > "$run_dir/job.raw"
        [[ -e "$run_dir/job.log" ]] || : > "$run_dir/job.log"

        result_tmp="$bridge_root/results/.${job_id}.tmp"
        result_dir="$bridge_root/results/$job_id"
        rm -rf "$result_tmp"
        mkdir -p "$result_tmp"
        tar -czf "$result_tmp/result.tar.gz" -C "$run_dir" job.raw job.log exit_code
        touch "$result_tmp/READY"
        mv "$result_tmp" "$result_dir"
        printf '%s finished %s rc=%s\n' "$(date -Is)" "$job_id" "$rc"
        rm -rf "$run_dir"
    done
    if (( handled == 0 )); then
        sleep 0.2
    fi
done

