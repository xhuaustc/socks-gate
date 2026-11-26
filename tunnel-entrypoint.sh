#!/bin/sh
# Copy the bind-mounted key to a 0600 path. OpenSSH rejects keys that are
# group/world-readable (common with bind mounts that inherit host 0644).
set -eu

KEY_SRC="${SSH_IDENTITY_FILE:-/run/ssh/id_ed25519}"
KEY_DST="/tmp/id_ed25519"

if [ ! -f "$KEY_SRC" ]; then
  echo "tunnel-entrypoint: missing identity file: $KEY_SRC" >&2
  exit 1
fi

# install(1) is not always present; fall back to cp + chmod.
if command -v install >/dev/null 2>&1; then
  install -m 600 "$KEY_SRC" "$KEY_DST"
else
  cp "$KEY_SRC" "$KEY_DST"
  chmod 600 "$KEY_DST"
fi

exec ssh -F /run/ssh/config -N -D 0.0.0.0:1080 "$@"
