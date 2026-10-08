#!/usr/bin/env bash
set -u

bridge_root="${MEQLAB_SPECTRE_BRIDGE_ROOT:-$HOME/.meqlab_spectre_bridge}"
outbox="$bridge_root/outbox"
inbox="$bridge_root/inbox"
wrapper_log="$bridge_root/wrapper.log"
mkdir -p "$outbox" "$inbox"

log_path=""
netlist_path=""
raw_path=""
previous=""
for argument in "$@"; do
    if [[ "$previous" == "=log" || "$previous" == "+log" ]]; then
        log_path="$argument"
    elif [[ "$previous" == "-r" ]]; then
        raw_path="$argument"
    elif [[ "$argument" == *.sp || "$argument" == *.scs ]]; then
        netlist_path="$argument"
    fi
    previous="$argument"
done

if [[ -z "$netlist_path" || -z "$raw_path" || -z "$log_path" ]]; then
    printf '%s invalid argv: %q\n' "$(date -Is)" "$*" >> "$wrapper_log"
    exit 64
fi

job_id="$(date +%s%N)-$$-$RANDOM"
stage="$outbox/.${job_id}.tmp"
request="$outbox/$job_id"
reply="$inbox/$job_id"
mkdir -p "$stage"
cp -f -- "$netlist_path" "$stage/job.sp"

model_path="$(sed -n "s/^[[:space:]]*\\.inc[[:space:]]*['\"]\\([^'\"]*\\)['\"].*/\\1/p" "$netlist_path" | head -n 1)"
if [[ -n "$model_path" && -f "$model_path" ]]; then
    cp -f -- "$model_path" "$stage/model.lib"
    sed -i "s|$model_path|model.lib|g" "$stage/job.sp"
    hdl_path="$(sed -n "s/^[[:space:]]*\\.hdl[[:space:]]*['\"]\\([^'\"]*\\)['\"].*/\\1/p" "$model_path" | head -n 1)"
    if [[ -n "$hdl_path" && -f "$hdl_path" ]]; then
        cp -f -- "$hdl_path" "$stage/asmhemt.va"
        sed -i "s|$hdl_path|asmhemt.va|g" "$stage/model.lib"
        for include_name in disciplines.vams constants.vams; do
            if [[ -f "$(dirname "$hdl_path")/$include_name" ]]; then
                cp -f -- "$(dirname "$hdl_path")/$include_name" "$stage/$include_name"
            fi
        done
    fi
fi

printf '%q\n' "$PWD" > "$stage/original_cwd"
printf '%s\n' "$job_id" > "$stage/JOB_ID"
touch "$stage/READY"
mv "$stage" "$request"
printf '%s submitted %s netlist=%s\n' "$(date -Is)" "$job_id" "$netlist_path" >> "$wrapper_log"

deadline=$((SECONDS + ${MEQLAB_SPECTRE_TIMEOUT_SECONDS:-1800}))
while [[ ! -f "$reply/READY" ]]; do
    if (( SECONDS >= deadline )); then
        printf '%s timeout %s\n' "$(date -Is)" "$job_id" >> "$wrapper_log"
        printf 'Spectre bridge timed out for job %s\n' "$job_id" > "$log_path"
        exit 124
    fi
    sleep 0.2
done

result_dir="$reply/extracted"
mkdir -p "$result_dir"
tar -xzf "$reply/result.tar.gz" -C "$result_dir"
rm -rf -- "$raw_path"
cp -a -- "$result_dir/job.raw" "$raw_path"
if [[ -f "$result_dir/job.log" ]]; then
    cp -f -- "$result_dir/job.log" "$log_path"
else
    : > "$log_path"
fi
exit_code=1
if [[ -f "$result_dir/exit_code" ]]; then
    exit_code="$(tr -cd '0-9' < "$result_dir/exit_code")"
fi
printf '%s completed %s rc=%s\n' "$(date -Is)" "$job_id" "${exit_code:-1}" >> "$wrapper_log"
rm -rf -- "$request" "$reply"
exit "${exit_code:-1}"

