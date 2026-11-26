# socks-gate

Local SOCKS5 gateway. One public SOCKS5 port load-balances across SSH dynamic tunnels (`ssh -D`). Edit `config.yml`, then run `./ctl.sh up`.

## Layout

```
config.yml              source of truth
generate.py             renders generated/*
ctl.sh                  render + docker compose lifecycle
Dockerfile              Alpine image with openssh-client
tunnel-entrypoint.sh    copies the SSH key to mode 0600, then runs ssh -N -D
check-socks5.sh         SOCKS5 handshake healthcheck for tunnel containers
generated/              rendered files; do not edit by hand
```

`generated/` is gitignored. Recreate it with `./ctl.sh render` or any command that starts the stack.

## Traffic path

```
client
  -> sing-box   (host listen_port, domain policy)
  -> HAProxy    (round-robin, SOCKS5 TCP check)
  -> ssh -D     (one container per host alias <user>-<port>)
  -> remote SSH server
```

| Piece | Image | Role |
| --- | --- | --- |
| `socks5` | `ghcr.io/sagernet/sing-box:v1.11.15` | Public SOCKS5 entry and domain routing |
| `socks5-lb` | `haproxy:2.9-alpine` | Pool health and round-robin |
| `socks5-<alias>` | `socks-gate:local` | SSH dynamic forward on container port 1080 |

HAProxy marks a backend down after failed SOCKS5 checks and drops sessions on that server. Compose project name is `socks-gate`.

## Prerequisites

- Docker with Compose v2
- Python 3 and PyYAML (`pip install pyyaml`)
- An SSH private key that can log in to every host in `config.yml` (BatchMode, no password prompt)

## Configure

`config.yml` fields:

| Field | Meaning |
| --- | --- |
| `ssh_private_key` | Absolute path to the private key. Bind-mounted read-only; the entrypoint copies it to `/tmp/id_ed25519` with mode `0600`. |
| `listen_port` | Host port published by sing-box. Container listen port stays `1080`. |
| `hosts` | SSH backends. Each item needs `user`, `hostname`, and `port`. Alias is always `<user>-<port>` and must be unique. |
| `domain.enabled` | `true` applies `domain.allow`. `false` sends all traffic through the tunnels. |
| `domain.default` | When filtering is on, non-matching destinations are `block` or `direct` (container egress, not the tunnel). |
| `domain.allow` | Domain suffixes. `example.com` also matches `a.example.com`. |

`enabled: true`, `default: block`, and an empty `allow` list is rejected, because it would block every destination.

## Operate

```sh
./ctl.sh up          # render config.yml and start
./ctl.sh restart     # re-render and recreate containers
./ctl.sh render      # only write generated/*
./ctl.sh status
./ctl.sh logs -f socks5
./ctl.sh stop        # stop, keep containers
./ctl.sh down        # stop and remove containers
```

Point a client at `127.0.0.1:<listen_port>` (SOCKS5, no authentication).

## Generated files

| File | Used by |
| --- | --- |
| `generated/ssh.conf` | OpenSSH in each tunnel container |
| `generated/haproxy.cfg` | `socks5-lb` |
| `generated/sing-box.json` | `socks5` route rules |
| `generated/compose.yml` | `./ctl.sh` via `docker compose` |
| `generated/domains.allow` | Text mirror of `domain.allow` (not mounted) |
