#!/bin/sh
set -eu
umask 077

usage() {
    printf '%s\n' 'Usage: start-buckyos-agent-daemon.sh [--download-only] [--artifact FILE --sha256 HASH] [daemon arguments or access URL]'
    printf '%s\n' 'Environment: BUCKYOS_AGENT_VERSION, BUCKYOS_AGENT_RELEASE_URL, BUCKYOS_AGENT_CACHE_DIR'
}

download_only=false
artifact=
digest=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --download-only) download_only=true; shift ;;
        --artifact) [ "$#" -ge 2 ] || exit 2; artifact=$2; shift 2 ;;
        --sha256) [ "$#" -ge 2 ] || exit 2; digest=$2; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) break ;;
    esac
done
if [ "$#" -eq 0 ] && [ "$download_only" = false ]; then usage; exit 2; fi
[ "$(uname -s)" = Linux ] || { printf '%s\n' 'This release supports Linux only.' >&2; exit 2; }
case "$(uname -m)" in
    x86_64) target=x86_64-unknown-linux-musl ;;
    aarch64|arm64) target=aarch64-unknown-linux-gnu ;;
    *) printf '%s\n' 'Unsupported Linux architecture.' >&2; exit 2 ;;
esac
version=${BUCKYOS_AGENT_VERSION:-0.7.0}
case "$version" in ''|*[!A-Za-z0-9._+-]*) printf '%s\n' 'Invalid version.' >&2; exit 2 ;; esac
cache=${BUCKYOS_AGENT_CACHE_DIR:-${XDG_CACHE_HOME:-${HOME:?}/.cache}/buckyos-agent-daemon}
mkdir -p "$cache"
chmod 700 "$cache"
temporary=$(mktemp -d "$cache/.download.XXXXXX")
trap 'rm -rf "$temporary"' EXIT HUP INT TERM
name=buckyos-agent-daemon-$version-$target.tar.gz
if [ -n "$artifact" ]; then
    [ -n "$digest" ] || { printf '%s\n' '--artifact requires --sha256.' >&2; exit 2; }
    cp "$artifact" "$temporary/$name"
else
    release=${BUCKYOS_AGENT_RELEASE_URL:-https://github.com/buckyos/buckyos/releases/download/agent-daemon-v$version}
    case "$release" in https://*) ;; *) printf '%s\n' 'Release URL must use HTTPS.' >&2; exit 2 ;; esac
    curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 "$release/$name" --output "$temporary/$name"
    if [ -z "$digest" ]; then
        curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 "$release/$name.sha256" --output "$temporary/checksum"
        digest=$(cut -d ' ' -f 1 < "$temporary/checksum")
    fi
fi
case "$digest" in ''|*[!0-9a-fA-F]*) printf '%s\n' 'Invalid SHA-256 digest.' >&2; exit 2 ;; esac
[ "${#digest}" -eq 64 ] || { printf '%s\n' 'Invalid SHA-256 digest length.' >&2; exit 2; }
printf '%s  %s\n' "$digest" "$temporary/$name" | sha256sum --check --status || { printf '%s\n' 'Artifact checksum mismatch.' >&2; exit 1; }
tar --extract --gzip --file "$temporary/$name" --directory "$temporary" --no-same-owner --no-same-permissions buckyos_agent_daemon manifest.json service.sh
[ -f "$temporary/buckyos_agent_daemon" ] && [ ! -L "$temporary/buckyos_agent_daemon" ] || exit 1
chmod 700 "$temporary/buckyos_agent_daemon" "$temporary/service.sh"
destination=$cache/$version-$target-$digest
if [ ! -d "$destination" ]; then
    mkdir "$temporary/install"
    mv "$temporary/buckyos_agent_daemon" "$temporary/manifest.json" "$temporary/service.sh" "$temporary/install/"
    mv "$temporary/install" "$destination"
fi
rm -rf "$temporary"
trap - EXIT HUP INT TERM
if [ "$download_only" = true ]; then printf '%s\n' "$destination/buckyos_agent_daemon"; exit 0; fi
case "$1" in wss://*|ws://*) access_url=$1; shift; set -- run --url "$access_url" "$@" ;; esac
exec "$destination/buckyos_agent_daemon" "$@"
