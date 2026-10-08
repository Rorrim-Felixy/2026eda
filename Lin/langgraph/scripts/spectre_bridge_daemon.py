"""Relay Spectre jobs between the MeQLab VM and the IC shared folder."""

from __future__ import annotations

import argparse
import os
import shutil
import time
from pathlib import Path

import paramiko


def mkdir_remote(sftp: paramiko.SFTPClient, path: str) -> None:
    current = ""
    for part in path.strip("/").split("/"):
        current += "/" + part
        try:
            sftp.stat(current)
        except OSError:
            sftp.mkdir(current)


def remote_exists(sftp: paramiko.SFTPClient, path: str) -> bool:
    try:
        sftp.stat(path)
        return True
    except OSError:
        return False


def download_remote_dir(
    sftp: paramiko.SFTPClient, remote_dir: str, local_dir: Path
) -> None:
    local_dir.mkdir(parents=True, exist_ok=True)
    for item in sftp.listdir_attr(remote_dir):
        remote_path = f"{remote_dir}/{item.filename}"
        local_path = local_dir / item.filename
        if item.st_mode & 0o040000:
            download_remote_dir(sftp, remote_path, local_path)
        else:
            sftp.get(remote_path, str(local_path))


def connect(args: argparse.Namespace) -> tuple[paramiko.SSHClient, paramiko.SFTPClient]:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        args.host,
        port=args.port,
        username=args.user,
        key_filename=str(args.key),
        look_for_keys=False,
        allow_agent=False,
        timeout=15,
    )
    return client, client.open_sftp()


def relay_once(sftp: paramiko.SFTPClient, args: argparse.Namespace) -> int:
    count = 0
    outbox = f"{args.remote_root}/outbox"
    inbox = f"{args.remote_root}/inbox"
    requests = args.local_root / "requests"
    results = args.local_root / "results"
    requests.mkdir(parents=True, exist_ok=True)
    results.mkdir(parents=True, exist_ok=True)
    mkdir_remote(sftp, outbox)
    mkdir_remote(sftp, inbox)

    for job_id in sftp.listdir(outbox):
        remote_job = f"{outbox}/{job_id}"
        ready = f"{remote_job}/READY"
        claimed = f"{remote_job}/CLAIMED"
        if not remote_exists(sftp, ready):
            continue
        try:
            sftp.rename(ready, claimed)
        except OSError:
            continue
        local_tmp = requests / f".{job_id}.tmp"
        local_job = requests / job_id
        shutil.rmtree(local_tmp, ignore_errors=True)
        download_remote_dir(sftp, remote_job, local_tmp)
        (local_tmp / "READY").touch()
        os.replace(local_tmp, local_job)
        print(f"submitted {job_id}", flush=True)
        count += 1

    for result_dir in results.iterdir():
        if not result_dir.is_dir() or not (result_dir / "READY").is_file():
            continue
        job_id = result_dir.name
        remote_tmp = f"{inbox}/.{job_id}.tmp"
        remote_job = f"{inbox}/{job_id}"
        if remote_exists(sftp, remote_job):
            continue
        mkdir_remote(sftp, remote_tmp)
        sftp.put(str(result_dir / "result.tar.gz"), f"{remote_tmp}/result.tar.gz")
        with sftp.file(f"{remote_tmp}/READY", "w") as marker:
            marker.write("ready\n")
        sftp.rename(remote_tmp, remote_job)
        shutil.rmtree(result_dir)
        print(f"returned {job_id}", flush=True)
        count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="192.168.25.129")
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--user", default="edaagent")
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--remote-root", default="/home/edaagent/.meqlab_spectre_bridge")
    parser.add_argument("--local-root", type=Path, default=Path(r"D:\codex_truth_bridge"))
    parser.add_argument("--poll-seconds", type=float, default=0.2)
    parser.add_argument("--status-file", type=Path)
    args = parser.parse_args()
    if args.status_file:
        args.status_file.parent.mkdir(parents=True, exist_ok=True)

    while True:
        client = None
        try:
            client, sftp = connect(args)
            print("connected", flush=True)
            while True:
                count = relay_once(sftp, args)
                if args.status_file:
                    args.status_file.write_text(
                        f"alive {time.time():.3f} relayed={count}\n", encoding="utf-8"
                    )
                time.sleep(args.poll_seconds)
        except KeyboardInterrupt:
            return
        except Exception as exc:
            print(f"bridge error: {exc!r}", flush=True)
            time.sleep(2.0)
        finally:
            if client is not None:
                client.close()


if __name__ == "__main__":
    main()

