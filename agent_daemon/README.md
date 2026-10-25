# Standalone Linux Agent Daemon

Build without the complete Node, Docker, Node.js or SDK distribution:

```bash
python3 src/publish/agent_daemon/package.py --target x86_64-unknown-linux-musl
python3 src/publish/agent_daemon/package.py --target aarch64-unknown-linux-gnu
```

The build host needs the existing Rust workspace C/C++ toolchain and Python 3.
The target needs a Linux kernel, CA certificates, a POSIX shell, curl, tar and
sha256sum for the launcher. The x86_64 binary is checked for static linking.
The aarch64 manifest records the actual minimum glibc symbol version and shared
libraries; run it only on systems providing those versions. No GUI components
are used; UI capability is always unsupported.

Publish the tar archive, adjacent `.sha256` checksum and launcher under the same
HTTPS release directory. Set `BUCKYOS_AGENT_RELEASE_URL` for a private release;
`BUCKYOS_AGENT_VERSION` defaults to 0.7.0. A release checksum is integrity checking
over trusted HTTPS, not a detached publisher signature.

```bash
./start-buckyos-agent-daemon.sh 'wss://zone/kapi/control-panel/executor/manage?executor_id=...#...'
./start-buckyos-agent-daemon.sh run --url-stdin
./start-buckyos-agent-daemon.sh --download-only
./start-buckyos-agent-daemon.sh --artifact ./release.tar.gz --sha256 '<64 hex characters>' doctor --json
```

Artifacts are cached below `$XDG_CACHE_HOME/buckyos-agent-daemon` (or
`~/.cache/buckyos-agent-daemon`) in version and digest directories. The launcher
never writes the URL to a file and uses `exec`, so Ctrl-C terminates the foreground
daemon directly. `--url-stdin` avoids putting the URL secret in the process command
line. No service, login entry, container or device identity is installed.

Upgrade: stop the active daemon, retain its data root and stable executor ID,
then run the new verified artifact. Incomplete ledger operations remain unknown
and must be queried; they are never resubmitted by the launcher. Rollback uses the
previous cached binary with the same data directory. Uninstall: stop the daemon,
remove its cache directory, and optionally remove its dedicated data directory
to discard the ledger. User business files and full Node files are outside this
cleanup. URL revocation and record deletion are separate control-plane commands.
