#!/usr/bin/env python3
"""Render generated/* from config.yml. User-facing config is config.yml only."""

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit("PyYAML is required: pip install pyyaml") from exc

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yml"
OUT_DIR = ROOT / "generated"

HEADER = "# GENERATED from config.yml by generate.py — do not edit by hand.\n"


def load_config() -> dict:
    if not CONFIG_PATH.is_file():
        raise SystemExit(
            f"missing config: {CONFIG_PATH}\n"
            f"  copy the template first: cp config.example.yml config.yml"
        )
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    validate(data)
    return data


def validate(cfg: dict) -> None:
    key = cfg.get("ssh_private_key")
    if not key or not isinstance(key, str):
        raise SystemExit("config.yml: ssh_private_key must be a non-empty string")

    listen_port = cfg.get("listen_port", 1081)
    if not isinstance(listen_port, int) or not (1 <= listen_port <= 65535):
        raise SystemExit("config.yml: listen_port must be an int 1-65535")

    hosts = cfg.get("hosts")
    if not isinstance(hosts, list) or not hosts:
        raise SystemExit("config.yml: hosts must be a non-empty list")

    aliases = []
    for i, host in enumerate(hosts):
        if not isinstance(host, dict):
            raise SystemExit(f"config.yml: hosts[{i}] must be a mapping")
        for field in ("user", "hostname", "port"):
            if field not in host:
                raise SystemExit(f"config.yml: hosts[{i}] missing '{field}'")
        if not isinstance(host["port"], int) or not (1 <= host["port"] <= 65535):
            raise SystemExit(f"config.yml: hosts[{i}].port must be an int 1-65535")
        alias = f"{host['user']}-{host['port']}"
        if alias in aliases:
            raise SystemExit(f"config.yml: duplicate host alias '{alias}'")
        aliases.append(alias)
        host["alias"] = alias

    domain = cfg.get("domain") or {}
    if not isinstance(domain, dict):
        raise SystemExit("config.yml: domain must be a mapping")
    enabled = bool(domain.get("enabled", True))
    default = domain.get("default", "block")
    if default not in ("block", "direct"):
        raise SystemExit("config.yml: domain.default must be 'block' or 'direct'")
    allow = domain.get("allow") or []
    if not isinstance(allow, list):
        raise SystemExit("config.yml: domain.allow must be a list")
    if enabled and default == "block" and not allow:
        raise SystemExit(
            "config.yml: domain.allow is empty while enabled=true and default=block "
            "(everything would be blocked)"
        )
    cfg["domain"] = {"enabled": enabled, "default": default, "allow": list(allow)}
    cfg["listen_port"] = listen_port
    cfg["_aliases"] = aliases


def write_ssh_conf(cfg: dict) -> None:
    lines = [
        HEADER.rstrip("\n"),
        "# Shared SSH options for all SOCKS5 tunnel nodes.",
        "Host *",
        "  IdentityFile /tmp/id_ed25519",
        "  IdentitiesOnly yes",
        "  BatchMode yes",
        "  ExitOnForwardFailure yes",
        "  StrictHostKeyChecking no",
        "  UserKnownHostsFile /dev/null",
        "  ServerAliveInterval 30",
        "  ServerAliveCountMax 3",
        "  ChannelTimeout direct-tcpip=10m",
        "",
        "# Alias format: <user>-<port>",
    ]
    for host in cfg["hosts"]:
        lines.extend(
            [
                f"Host {host['alias']}",
                f"  HostName {host['hostname']}",
                f"  User {host['user']}",
                f"  Port {host['port']}",
                "",
            ]
        )
    (OUT_DIR / "ssh.conf").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_haproxy_cfg(cfg: dict) -> None:
    servers = "\n".join(
        f"  server {alias} socks5-{alias}:1080 check"
        for alias in cfg["_aliases"]
    )
    depends_comment = ", ".join(cfg["_aliases"])
    text = f"""{HEADER}global
  log stdout format raw local0
  maxconn 4096

defaults
  mode tcp
  log global
  timeout connect 3s
  timeout client  10m
  timeout server  10m
  timeout check   2s
  option redispatch
  retries 2

frontend health
  bind *:8404
  mode http
  monitor-uri /healthz
  monitor fail if {{ nbsrv(socks5_pool) eq 0 }}

frontend socks5_in
  bind *:1080
  default_backend socks5_pool

backend socks5_pool
  balance roundrobin
  option redispatch
  option tcp-check
  tcp-check connect
  tcp-check send-binary 050100
  tcp-check expect binary 05
  default-server inter 3s fall 2 rise 2 on-marked-down shutdown-sessions observe layer4 error-limit 2 on-error mark-down
  # backends: {depends_comment}
{servers}
"""
    (OUT_DIR / "haproxy.cfg").write_text(text, encoding="utf-8")


def write_sing_box(cfg: dict) -> None:
    domain = cfg["domain"]
    outbounds = [
        {
            "type": "socks",
            "tag": "upstream",
            "server": "socks5-lb",
            "server_port": 1080,
            "version": "5",
        },
        {"type": "block", "tag": "block"},
        {"type": "direct", "tag": "direct"},
    ]

    rules = []
    if domain["enabled"]:
        if domain["allow"]:
            rules.append(
                {
                    "domain_suffix": list(domain["allow"]),
                    "outbound": "upstream",
                }
            )
        final = domain["default"]
    else:
        # No domain filter: everything through SSH tunnels.
        final = "upstream"

    doc = {
        "log": {"level": "info", "timestamp": True},
        "inbounds": [
            {
                "type": "socks",
                "tag": "socks-in",
                "listen": "0.0.0.0",
                "listen_port": 1080,
                "sniff": True,
                "sniff_override_destination": True,
            }
        ],
        "outbounds": outbounds,
        "route": {"rules": rules, "final": final},
    }
    payload = json.dumps(doc, indent=2) + "\n"
    (OUT_DIR / "sing-box.json").write_text(payload, encoding="utf-8")


def write_compose(cfg: dict) -> None:
    key = cfg["ssh_private_key"]
    listen_port = cfg["listen_port"]
    aliases = cfg["_aliases"]

    tunnel_services = []
    for alias in aliases:
        tunnel_services.append(
            f"""  socks5-{alias}:
    <<: *socks5-ssh
    container_name: socks-gate-{alias}
    command: ["{alias}"]
"""
        )

    depends = "\n".join(
        f"      socks5-{alias}:\n        condition: service_started" for alias in aliases
    )

    text = f"""{HEADER}# Project directory should be the parent of generated/ (see ./ctl.sh).

x-socks5-ssh: &socks5-ssh
  build: ..
  image: socks-gate:local
  restart: unless-stopped
  volumes:
    - {key}:/run/ssh/id_ed25519:ro
    - ./ssh.conf:/run/ssh/config:ro
    - ../check-socks5.sh:/usr/local/bin/check-socks5.sh:ro
    - ../tunnel-entrypoint.sh:/usr/local/bin/tunnel-entrypoint.sh:ro
  entrypoint: ["/bin/sh", "/usr/local/bin/tunnel-entrypoint.sh"]
  expose:
    - "1080"
  healthcheck:
    test: ["CMD-SHELL", "sh /usr/local/bin/check-socks5.sh"]
    interval: 5s
    timeout: 3s
    retries: 2
    start_period: 15s

services:
{''.join(tunnel_services)}  socks5-lb:
    image: haproxy:2.9-alpine
    container_name: socks-gate-lb
    restart: unless-stopped
    expose:
      - "1080"
      - "8404"
    volumes:
      - ./haproxy.cfg:/usr/local/etc/haproxy/haproxy.cfg:ro
    healthcheck:
      test:
        [
          "CMD-SHELL",
          "wget -q -O /dev/null http://127.0.0.1:8404/healthz || exit 1",
        ]
      interval: 5s
      timeout: 3s
      retries: 3
      start_period: 20s
    depends_on:
{depends}

  socks5:
    image: ghcr.io/sagernet/sing-box:v1.11.15
    container_name: socks-gate
    restart: unless-stopped
    ports:
      - "{listen_port}:1080"
    volumes:
      - ./sing-box.json:/etc/sing-box/config.json:ro
    command: ["run", "-c", "/etc/sing-box/config.json"]
    healthcheck:
      test: ["CMD", "sing-box", "version"]
      interval: 5s
      timeout: 3s
      retries: 2
      start_period: 10s
    depends_on:
      socks5-lb:
        condition: service_healthy
"""
    (OUT_DIR / "compose.yml").write_text(text, encoding="utf-8")


def write_domains_allow(cfg: dict) -> None:
    lines = [
        "# GENERATED from config.yml — mirror of domain.allow",
        f"# enabled={cfg['domain']['enabled']} default={cfg['domain']['default']}",
    ]
    lines.extend(cfg["domain"]["allow"])
    (OUT_DIR / "domains.allow").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    cfg = load_config()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_ssh_conf(cfg)
    write_haproxy_cfg(cfg)
    write_sing_box(cfg)
    write_compose(cfg)
    write_domains_allow(cfg)
    print(f"generated {OUT_DIR}/ from {CONFIG_PATH.name}")
    print(f"  hosts: {', '.join(cfg['_aliases'])}")
    print(
        f"  domain: enabled={cfg['domain']['enabled']} "
        f"default={cfg['domain']['default']} "
        f"allow={len(cfg['domain']['allow'])}"
    )
    print(f"  listen_port: {cfg['listen_port']}")
    print(f"  ssh_private_key: {cfg['ssh_private_key']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
