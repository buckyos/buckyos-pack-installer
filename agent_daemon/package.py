#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile

TARGETS = ("x86_64-unknown-linux-musl", "aarch64-unknown-linux-gnu")


def main():
    parser = argparse.ArgumentParser(description="Build a standalone Linux Agent Daemon release")
    parser.add_argument("--target", choices=TARGETS, required=True)
    parser.add_argument("--binary", type=Path, help="Package an existing target binary")
    parser.add_argument("--output", type=Path, default=Path("dist/agent-daemon"))
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[2]
    version = (source / "VERSION").read_text().strip()
    if not re.fullmatch(r"[A-Za-z0-9._+-]+", version):
        raise SystemExit("Invalid src/VERSION")
    if args.binary is None:
        subprocess.run(["cargo", "build", "--release", "--target", args.target,
                        "-p", "buckyos_agent_daemon"], cwd=source, check=True)
        binary = Path(os.environ.get("CARGO_TARGET_DIR", source / "target")) / args.target / "release/buckyos_agent_daemon"
    else:
        binary = args.binary.resolve()
    elf = subprocess.check_output(["readelf", "-h", str(binary)], text=True)
    machine = "AArch64" if args.target.startswith("aarch64") else "Advanced Micro Devices X86-64"
    if machine not in elf:
        raise SystemExit("Binary architecture does not match requested target")
    dynamic = subprocess.check_output(["readelf", "-d", str(binary)], text=True)
    libraries = sorted(set(re.findall(r"Shared library: \[(.*?)\]", dynamic)))
    if args.target.endswith("musl") and libraries:
        raise SystemExit(f"musl release must be static; found {libraries}")
    symbols = subprocess.check_output(["readelf", "--version-info", str(binary)], text=True)
    versions = re.findall(r"GLIBC_(\d+\.\d+(?:\.\d+)?)", symbols)
    glibc = max(versions, key=lambda v: tuple(map(int, v.split(".")))) if versions else None
    manifest = {
        "product": "buckyos_agent_daemon", "version": version, "target": args.target,
        "os": "linux", "capabilities": ["exec", "read", "write", "edit", "query", "cancel"],
        "ui": "unsupported", "dependencies": libraries, "minimum_glibc": glibc,
        "files": ["buckyos_agent_daemon", "manifest.json", "service.sh"],
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "signature": "HTTPS release with SHA-256 checksum; no detached signing configured",
    }
    args.output.mkdir(parents=True, exist_ok=True)
    archive = args.output / f"buckyos-agent-daemon-{version}-{args.target}.tar.gz"
    with tempfile.TemporaryDirectory() as temporary:
        stage = Path(temporary)
        shutil.copyfile(binary, stage / "buckyos_agent_daemon")
        (stage / "buckyos_agent_daemon").chmod(0o755)
        shutil.copyfile(Path(__file__).with_name("service.sh"), stage / "service.sh")
        (stage / "service.sh").chmod(0o755)
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        with tarfile.open(archive, "w:gz") as output:
            for name in manifest["files"]:
                output.add(stage / name, arcname=name)
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(archive.suffix + ".sha256").write_text(f"{checksum}  {archive.name}\n")
    shutil.copyfile(Path(__file__).with_name("start-buckyos-agent-daemon.sh"), args.output / "start-buckyos-agent-daemon.sh")
    print(archive)


if __name__ == "__main__":
    main()
