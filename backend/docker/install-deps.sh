#!/bin/sh
# Installs the locked dependencies into the image. Designed for slow or unreliable connections to the package index:
#   1. Downloads go to BuildKit cache directories that survive failed builds, so every attempt resumes instead of starting over.
#   2. If everything is already there (or was pre-downloaded with `make wheelhouse`), nothing is fetched at all.
#   3. Network failures are retried a bounded number of times; then the build FAILS with a clear message.
#   4. It never skips a dependency: the final install is offline, hash-checked, and must contain every package in the lock file.
# The build backend (setuptools, pinned in the lock) is installed first, so the one dependency published only as source
# (jstyleson, required by extruct) is built with it locally instead of pip fetching a build backend from the index again.
set -eu

LOCK="${1:?usage: install-deps.sh <requirements.lock>}"
CACHE=/var/cache/wheelhouse           # BuildKit cache mount
LOCAL=/ctx/wheelhouse                 # optional files downloaded on the host (backend/wheelhouse)
ATTEMPTS="${PIP_DOWNLOAD_ATTEMPTS:-4}"
INDEX="${PIP_INDEX_URL:-https://pypi.org/simple}"
BUILD_LOCK=/tmp/build-backend.lock
NETWORK_ERRORS='Timeout|timed out|SSLError|SSLEOF|EOF occurred|ConnectionError|Connection (reset|aborted|refused|broken)|ProxyError|NewConnectionError|Temporary failure|Name or service not known|RemoteDisconnected|IncompleteRead|ProtocolError'

mkdir -p "$CACHE"
FIND="--find-links $CACHE"
if [ -d "$LOCAL" ]; then FIND="$FIND --find-links $LOCAL"; fi
# The setuptools entry of the lock file (its line plus the indented hash lines that follow it).
awk '/^[^ #]/{p=($0 ~ /^setuptools==/)} p' "$LOCK" > "$BUILD_LOCK"
[ -s "$BUILD_LOCK" ] || { echo "deps: FAILED. setuptools is missing from $LOCK (run 'make lock')."; exit 1; }
total=$(grep -cE '^[A-Za-z0-9_.-]+==' "$LOCK")

# shellcheck disable=SC2086
offline() { pip install --no-index $FIND --require-hashes --no-deps --no-build-isolation -r "$1"; }

# Runs a download command; retries only when the failure is a network failure.
fetch() {
    what="$1"; shift
    n=1
    while :; do
        if "$@" >/tmp/download.log 2>&1; then return 0; fi
        grep -E "ERROR|WARNING: Retrying" /tmp/download.log | tail -4 | cut -c1-300 || true
        if ! grep -qE "$NETWORK_ERRORS" /tmp/download.log; then
            echo "deps: FAILED while downloading $what. This is not a network error (see the pip message above); retrying would not help."
            exit 1
        fi
        if [ "$n" -ge "$ATTEMPTS" ]; then
            echo ""
            echo "deps: FAILED. The package index could not be reached reliably after $ATTEMPTS attempts: $INDEX"
            echo "deps: this is a network problem between Docker and the package index, not an application error."
            echo "deps: files already downloaded are kept in the build cache; running the build again resumes from there."
            echo "deps: options: (1) run the build again; (2) use a reachable mirror: PIP_INDEX_URL=https://<mirror>/simple docker compose build;"
            echo "deps:          (3) run 'make wheelhouse' to download with this computer's own connection, then build again (no index needed)."
            exit 1
        fi
        echo "deps: network failure on attempt $n of $ATTEMPTS while downloading $what; retrying in 10s (completed downloads are kept)"
        n=$((n + 1))
        sleep 10
    done
}

# 1. Build backend.
if ! offline "$BUILD_LOCK" >/tmp/offline.log 2>&1; then
    echo "deps: downloading from $INDEX (timeout ${PIP_DEFAULT_TIMEOUT:-15}s and ${PIP_RETRIES:-5} retries per request, up to $ATTEMPTS attempts)"
    fetch "the build backend" pip download --only-binary=:all: --require-hashes --no-deps -r "$BUILD_LOCK" -d "$CACHE"
    offline "$BUILD_LOCK" >/tmp/offline.log 2>&1 || { cat /tmp/offline.log; exit 1; }
fi

# 2. Everything else: offline if the files are already here, otherwise download (bounded retries) and then install offline.
if offline "$LOCK" >/tmp/offline.log 2>&1; then
    echo "deps: all $total locked packages installed from local files. The package index was not contacted for them."
    exit 0
fi
echo "deps: local files are incomplete; downloading the locked packages from $INDEX"
fetch "the locked packages" pip download --prefer-binary --require-hashes --no-deps --no-build-isolation -r "$LOCK" -d "$CACHE"
offline "$LOCK" >/tmp/offline.log 2>&1 || { tail -20 /tmp/offline.log; echo "deps: FAILED. The downloaded files could not be installed (see above)."; exit 1; }
echo "deps: all $total locked packages downloaded and installed."
