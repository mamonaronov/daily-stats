#!/usr/bin/env bash
# Apply repo configs on this host: mihomo, systemd units, .env VPN keys,
# docker-compose.override.yml, then build and start the bot.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=deploy/lib.sh
source "$ROOT/deploy/lib.sh"

SKIP_DOCKER=0
SKIP_MIHOMO=0

usage() {
  cat <<EOF
Usage: $(basename "$0") [--skip-docker] [--skip-mihomo]

Applies configs from the repository:

  deploy/mihomo/config.yaml   -> /etc/mihomo/config.yaml
  deploy/mihomo/mihomo.service -> /etc/systemd/system/mihomo.service
  deploy/daily-stats.service  -> /etc/systemd/system/daily-stats.service
  deploy/daily-stats-update.timer -> systemd: git pull origin/main + rebuild
  .env                        VPN keys synced from mihomo config
  docker-compose.override.yml from mihomo mixed-port (not v2rayN 10808)

Then enables systemd units and runs docker compose up -d --build.

After this once, a push to main is picked up by the timer: deploy/update.sh
notifies the owner before rebuild, after the bot is ready, and on failure.

  --skip-docker   only apply host configs / mihomo, do not touch the container
  --skip-mihomo   skip copying and restarting mihomo
EOF
}

for arg in "$@"; do
  case "$arg" in
    --skip-docker) SKIP_DOCKER=1 ;;
    --skip-mihomo) SKIP_MIHOMO=1 ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown argument: $arg" >&2
      usage >&2
      exit 1
      ;;
  esac
done

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "error: '$1' not found" >&2
    exit 1
  }
}

echo "==> reading deploy/mihomo/config.yaml"
load_mihomo_settings
echo "    mixed-port ${MIXED_PORT}"
echo "    API        ${MIHOMO_API_URL}"

need_cmd docker
need_cmd systemctl
if [[ "$SKIP_MIHOMO" -eq 0 ]]; then
  if ! command -v mihomo >/dev/null 2>&1; then
    echo "error: mihomo not found. Install it first: yay -S mihomo" >&2
    exit 1
  fi
fi
if [[ "$(id -u)" -ne 0 ]] && ! command -v sudo >/dev/null 2>&1; then
  echo "error: need root or sudo to install systemd units and /etc/mihomo" >&2
  exit 1
fi

if [[ ! -f "$ROOT/.env" ]]; then
  echo "==> creating .env from .env.example"
  cp "$ROOT/.env.example" "$ROOT/.env"
fi

echo "==> syncing VPN keys in .env"
upsert_env TELEGRAM_PROXY_URL "socks5://127.0.0.1:${MIXED_PORT}" "$ROOT/.env"
upsert_env MIHOMO_API_URL "$MIHOMO_API_URL" "$ROOT/.env"
if [[ -n "${MIHOMO_SECRET:-}" ]]; then
  upsert_env MIHOMO_API_SECRET "$MIHOMO_SECRET" "$ROOT/.env"
fi

bot_token="$(awk -F= '/^BOT_TOKEN=/ {sub(/^BOT_TOKEN=/, ""); print; exit}' "$ROOT/.env")"
owner_id="$(awk -F= '/^OWNER_TELEGRAM_ID=/ {sub(/^OWNER_TELEGRAM_ID=/, ""); print; exit}' "$ROOT/.env")"
if [[ -z "$bot_token" || "$bot_token" == 1234567890:* ]]; then
  echo "error: fill BOT_TOKEN in $ROOT/.env before deploy" >&2
  exit 1
fi
if [[ -z "$owner_id" || "$owner_id" == "123456789" ]]; then
  echo "error: fill OWNER_TELEGRAM_ID in $ROOT/.env before deploy" >&2
  exit 1
fi

echo "==> ensuring data/ and backups/"
mkdir -p "$ROOT/data" "$ROOT/backups"

echo "==> writing docker-compose.override.yml"
write_docker_override "$ROOT/docker-compose.override.yml"

echo "==> git commit for image"
load_git_version
echo "    ${GIT_COMMIT} ${GIT_COMMIT_TITLE}"

if [[ "$SKIP_MIHOMO" -eq 0 ]]; then
  echo "==> installing mihomo config and unit"
  run_sudo mkdir -p /etc/mihomo/providers
  mihomo_changed=1
  if [[ -f /etc/mihomo/config.yaml ]] && cmp -s "$ROOT/deploy/mihomo/config.yaml" /etc/mihomo/config.yaml; then
    mihomo_changed=0
  fi
  run_sudo install -m 644 "$ROOT/deploy/mihomo/config.yaml" /etc/mihomo/config.yaml
  run_sudo install -m 644 "$ROOT/deploy/mihomo/mihomo.service" /etc/systemd/system/mihomo.service
fi

echo "==> installing daily-stats.service (WorkingDirectory=$ROOT)"
docker_bin="$(command -v docker)"
unit_tmp="$(mktemp)"
awk -v wd="$ROOT" -v docker="$docker_bin" '
  /^WorkingDirectory=/ { print "WorkingDirectory=" wd; next }
  /^ExecStart=/ { print "ExecStart=" docker " compose up -d"; next }
  /^ExecStop=/ { print "ExecStop=" docker " compose stop"; next }
  { print }
' "$ROOT/deploy/daily-stats.service" > "$unit_tmp"
run_sudo install -m 644 "$unit_tmp" /etc/systemd/system/daily-stats.service
rm -f "$unit_tmp"

echo "==> installing daily-stats-update.timer (WorkingDirectory=$ROOT)"
chmod +x "$ROOT/deploy/update.sh"
update_tmp="$(mktemp)"
awk -v wd="$ROOT" '
  /^WorkingDirectory=/ { print "WorkingDirectory=" wd; next }
  /^ExecStart=/ { print "ExecStart=" wd "/deploy/update.sh"; next }
  { print }
' "$ROOT/deploy/daily-stats-update.service" > "$update_tmp"
run_sudo install -m 644 "$update_tmp" /etc/systemd/system/daily-stats-update.service
run_sudo install -m 644 "$ROOT/deploy/daily-stats-update.timer" /etc/systemd/system/daily-stats-update.timer
rm -f "$update_tmp"

echo "==> systemd daemon-reload"
run_sudo systemctl daemon-reload

if [[ "$SKIP_MIHOMO" -eq 0 ]]; then
  echo "==> enabling mihomo"
  run_sudo systemctl enable mihomo.service
  if [[ "${mihomo_changed:-1}" -eq 0 ]] && systemctl is-active --quiet mihomo.service; then
    echo "==> mihomo config unchanged, skip restart"
  else
    echo "==> restarting mihomo"
    run_sudo systemctl restart mihomo.service
  fi
  echo "==> waiting for mixed-port ${MIXED_PORT}"
  if ! wait_for_port "$MIXED_PORT" 30; then
    echo "error: mihomo did not listen on 127.0.0.1:${MIXED_PORT}" >&2
    run_sudo systemctl --no-pager --full status mihomo.service | tail -n 40 >&2 || true
    exit 1
  fi
  echo "==> waiting for Telegram via SOCKS 127.0.0.1:${MIXED_PORT}"
  if wait_for_telegram_via_socks "$MIXED_PORT" 90; then
    echo "    api.telegram.org reachable through mihomo"
  else
    echo "warning: api.telegram.org is not reachable through socks5://127.0.0.1:${MIXED_PORT}" >&2
    echo "warning: AUTO may still be probing nodes; the bot will keep retrying" >&2
  fi
fi

run_sudo systemctl enable daily-stats.service
run_sudo systemctl enable --now daily-stats-update.timer

if [[ "$SKIP_DOCKER" -eq 1 ]]; then
  echo "==> skip docker"
else
  echo "==> docker compose up -d --build"
  (
    cd "$ROOT"
    docker compose up -d --build
  )
  run_sudo systemctl start daily-stats.service
fi

echo
echo "done"
echo "  v2rayN        10808 / 10809 (untouched)"
echo "  mihomo mixed  127.0.0.1:${MIXED_PORT}"
echo "  mihomo API    ${MIHOMO_API_URL}"
if [[ "$SKIP_MIHOMO" -eq 0 ]]; then
  echo "  mihomo unit   $(systemctl is-active mihomo.service 2>/dev/null || echo unknown)"
fi
if [[ "$SKIP_DOCKER" -eq 0 ]]; then
  echo "  container     $(docker inspect -f '{{.State.Status}}' daily-stats-bot 2>/dev/null || echo not-created)"
fi
echo
echo "check:"
echo "  curl -s --max-time 8 -x socks5h://127.0.0.1:${MIXED_PORT} https://api.telegram.org"
echo "  docker compose -f \"$ROOT/docker-compose.yml\" logs -f bot"
