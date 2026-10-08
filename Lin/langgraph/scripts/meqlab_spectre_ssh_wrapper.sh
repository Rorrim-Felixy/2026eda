#!/usr/bin/env bash
set -u

ic_host="${MEQLAB_SPECTRE_HOST:-192.168.25.134}"
ic_user="${MEQLAB_SPECTRE_USER:-meow}"
ic_key="${MEQLAB_SPECTRE_KEY:-$HOME/.ssh/meqlab_to_ic_ed25519}"
spectre_bin="${MEQLAB_SPECTRE_BIN:-/opt/cadence/SPECTRE231/bin/spectre}"
license_file="${MEQLAB_SPECTRE_LICENSE:-/opt/cadence/IC618/share/license/license.dat}"
timeout_seconds="${MEQLAB_SPECTRE_TIMEOUT_SECONDS:-1800}"
asmhemt_model_dir="${MEQLAB_ASMHEMT_DIR:-$HOME/asmhemt101_6}"
wrapper_log="${MEQLAB_SPECTRE_LOG:-$HOME/.meqlab_spectre_wrapper.log}"
ssh_opts=(-i "$ic_key" -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=10)

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
local_stage="$(mktemp -d "$HOME/.cache/meqlab-spectre-${job_id}.XXXXXX")"
remote_stage="/home/$ic_user/meqlab-spectre-jobs/$job_id"
trap 'rm -rf -- "$local_stage"' EXIT
cp -f -- "$netlist_path" "$local_stage/job.sp"

# MeQLab emits the generic MOS compact-model form with four external and four
# internal nodes.  ASM-HEMT is a four-terminal Verilog-A model in isothermal
# mode, so keep the internal nodes (where MeQLab's current probes connect) and
# remove the duplicate external node quartet from the device instance.
awk '
    BEGIN { spice_language = 0 }
    /^[[:space:]]*simulator[[:space:]]+lang[[:space:]]*=[[:space:]]*spice/ {
        spice_language = 1
        print
        next
    }
    /^[[:space:]]*simulator[[:space:]]+lang[[:space:]]*=[[:space:]]*spectre/ {
        spice_language = 0
        print
        next
    }
    spice_language && /^[[:space:]]*parameters[[:space:]]+opt_par_/ {
        sub(/^[[:space:]]*parameters[[:space:]]+/, ".param ")
        print
        next
    }
    tolower($1) == "x1" && $10 == "asmhemt_blind" {
        printf "%s %s %s %s %s", $1, $2, $3, $4, $5
        for (field = 10; field <= NF; field++) printf " %s", $field
        printf "\n"
        next
    }
    { print }
' "$local_stage/job.sp" > "$local_stage/job.sp.rewritten"
mv "$local_stage/job.sp.rewritten" "$local_stage/job.sp"

model_path="$(sed -n "s/^[[:space:]]*\\.inc[[:space:]]*['\"]\\([^'\"]*\\)['\"].*/\\1/p" "$netlist_path" | head -n 1)"
if [[ -n "$model_path" && -f "$model_path" ]]; then
    cp -f -- "$model_path" "$local_stage/model.lib"
    sed -i "s|$model_path|model.lib|g" "$local_stage/job.sp"
    hdl_reference="$(sed -n "s/^[[:space:]]*\\.hdl[[:space:]]*['\"]\\([^'\"]*\\)['\"].*/\\1/p" "$model_path" | head -n 1)"
    hdl_path="$hdl_reference"
    if [[ -n "$hdl_path" && "$hdl_path" != /* ]]; then
        relative_hdl_path="$(dirname "$model_path")/$hdl_path"
        if [[ -f "$relative_hdl_path" ]]; then
            hdl_path="$relative_hdl_path"
        else
            hdl_path="$asmhemt_model_dir/$hdl_reference"
        fi
    fi
    if [[ -n "$hdl_path" && -f "$hdl_path" ]]; then
        cp -f -- "$hdl_path" "$local_stage/asmhemt.va"
        sed -i "s|$hdl_reference|asmhemt.va|g" "$local_stage/model.lib"
        for include_name in disciplines.vams constants.vams; do
            if [[ -f "$(dirname "$hdl_path")/$include_name" ]]; then
                cp -f -- "$(dirname "$hdl_path")/$include_name" "$local_stage/$include_name"
            fi
        done
    fi
fi

printf '%s submit %s netlist=%s\n' "$(date -Is)" "$job_id" "$netlist_path" >> "$wrapper_log"
ssh "${ssh_opts[@]}" "$ic_user@$ic_host" "mkdir -p '$remote_stage'" || exit 71
scp -q "${ssh_opts[@]}" "$local_stage"/* "$ic_user@$ic_host:$remote_stage/" || exit 72

timeout "$timeout_seconds" ssh "${ssh_opts[@]}" "$ic_user@$ic_host" \
    "cd '$remote_stage'; cache=/home/$ic_user/.cache/meqlab-asmhemt-job.ahdlSimDB; test ! -d \"\$cache\" || cp -a \"\$cache\" job.ahdlSimDB; CDS_LIC_FILE='$license_file' LM_LICENSE_FILE='$license_file' '$spectre_bin' -64 =log job.log job.sp -r job.raw; rc=\$?; if test -d job.ahdlSimDB && test ! -d \"\$cache\"; then mkdir -p /home/$ic_user/.cache; cp -a job.ahdlSimDB \"\$cache\"; fi; printf '%s\\n' \$rc > exit_code; test -e job.raw || : > job.raw; test -e job.log || : > job.log; tar -czf result.tar.gz job.raw job.log exit_code; exit 0"
transport_rc=$?
if (( transport_rc != 0 )); then
    printf '%s remote transport failure %s rc=%s\n' "$(date -Is)" "$job_id" "$transport_rc" >> "$wrapper_log"
    printf 'Remote Spectre transport failed for %s (rc=%s)\n' "$job_id" "$transport_rc" > "$log_path"
    exit "$transport_rc"
fi

scp -q "${ssh_opts[@]}" "$ic_user@$ic_host:$remote_stage/result.tar.gz" "$local_stage/result.tar.gz" || exit 73
tar -xzf "$local_stage/result.tar.gz" -C "$local_stage"
rm -rf -- "$raw_path"
cp -a -- "$local_stage/job.raw" "$raw_path"
cp -f -- "$local_stage/job.log" "$log_path"
exit_code="$(tr -cd '0-9' < "$local_stage/exit_code")"
if [[ "${exit_code:-1}" != "0" ]]; then
    failure_dir="$HOME/.meqlab_spectre_failures/$job_id"
    mkdir -p "$failure_dir"
    cp -a -- "$local_stage"/. "$failure_dir"/
fi
ssh "${ssh_opts[@]}" "$ic_user@$ic_host" "rm -rf '$remote_stage'" >/dev/null 2>&1 || true
printf '%s completed %s rc=%s\n' "$(date -Is)" "$job_id" "${exit_code:-1}" >> "$wrapper_log"
exit "${exit_code:-1}"
