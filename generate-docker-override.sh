#!/usr/bin/env bash
# Write docker-compose.override.yml from deploy/mihomo/config.yaml ports.
# Does not probe 10808 — that belongs to v2rayN.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=deploy/lib.sh
source "$ROOT/deploy/lib.sh"

load_mihomo_settings
write_docker_override "$ROOT/docker-compose.override.yml"

echo "wrote $ROOT/docker-compose.override.yml"
echo "  SOCKS5  127.0.0.1:${MIXED_PORT}  -> TELEGRAM_PROXY_URL"
echo "  API     ${MIHOMO_API_URL}"

if port_listening "$MIXED_PORT"; then
  echo "  mihomo mixed-port ${MIXED_PORT} is listening"
else
  echo "warning: nothing listens on ${MIXED_PORT}; start mihomo (or run ./deploy.sh)" >&2
fi
