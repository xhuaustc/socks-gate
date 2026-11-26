#!/usr/bin/env sh
# Maintain the SOCKS5 stack. Source of truth is config.yml.
set -eu
cd "$(dirname "$0")"

PROJECT=socks-gate
COMPOSE_FILE=generated/compose.yml

usage() {
  cat <<'EOF'
Usage: ./ctl.sh <command> [args]

  up, start       Render config.yml and start the stack
  stop            Stop containers (keep them)
  down            Stop and remove containers
  restart         Re-render config.yml and recreate containers
  status, ps      Show container status
  logs [svc...]   Show logs (extra args go to docker compose logs)
  render          Only regenerate generated/*
  help            Show this help

Examples:
  ./ctl.sh up
  ./ctl.sh status
  ./ctl.sh logs -f socks5
  ./ctl.sh restart
  ./ctl.sh stop
EOF
}

compose() {
  docker compose \
    -p "$PROJECT" \
    --project-directory "$(pwd)/generated" \
    -f "$COMPOSE_FILE" \
    "$@"
}

need_compose() {
  if [ ! -f "$COMPOSE_FILE" ]; then
    echo "missing $COMPOSE_FILE — run ./ctl.sh up or ./ctl.sh render first" >&2
    exit 1
  fi
}

cmd=${1:-help}
if [ "$#" -gt 0 ]; then
  shift
fi

case "$cmd" in
  up|start)
    python3 ./generate.py
    compose up -d --build --remove-orphans "$@"
    ;;
  stop)
    need_compose
    compose stop "$@"
    ;;
  down)
    need_compose
    compose down --remove-orphans "$@"
    # container_name is fixed; leftovers from other compose projects survive
    # project-scoped down. Remove by name prefix so containers are gone.
    leftover=$(docker ps -aq --filter "name=socks-gate" 2>/dev/null || true)
    if [ -n "$leftover" ]; then
      # Word-split intentional: docker IDs separated by whitespace.
      # shellcheck disable=SC2086
      docker rm -f $leftover
    fi
    ;;
  restart)
    python3 ./generate.py
    compose up -d --build --force-recreate --remove-orphans "$@"
    ;;
  status|ps)
    need_compose
    compose ps "$@"
    ;;
  logs)
    need_compose
    compose logs "$@"
    ;;
  render|generate)
    python3 ./generate.py
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    echo "unknown command: $cmd" >&2
    usage
    exit 1
    ;;
esac
