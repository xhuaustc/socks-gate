#!/bin/sh
# Local SOCKS5 healthcheck for tunnel containers.
# Prefer a real SOCKS5 handshake; fall back to TCP / process liveness.
set -eu

HOST=127.0.0.1
PORT=1080

socks5_python() {
  "$1" - "$HOST" "$PORT" <<'PY'
import socket, sys
host, port = sys.argv[1], int(sys.argv[2])
s = socket.create_connection((host, port), 2)
try:
    s.sendall(b"\x05\x01\x00")
    data = s.recv(2)
finally:
    s.close()
sys.exit(0 if data and data[0] == 5 else 1)
PY
}

if command -v python3 >/dev/null 2>&1; then
  socks5_python python3
elif command -v python >/dev/null 2>&1; then
  socks5_python python
elif command -v bash >/dev/null 2>&1; then
  bash -c "exec 3<>/dev/tcp/${HOST}/${PORT}"
elif command -v nc >/dev/null 2>&1; then
  nc -z "$HOST" "$PORT"
else
  # Entrypoint is ssh; if PID 1 is gone the container is exiting anyway.
  kill -0 1
fi
