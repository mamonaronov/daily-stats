#!/usr/bin/env bash
# Apply systemd unit, then build and start the bot container.
# SOCKS/API live in a separate mihomo-proxy compose — this repo is the bot only.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=deploy/lib.sh
source "$ROOT/deploy/lib.sh"

SKIP_DOCKER=0

usage() {
  cat <<EOF
Usage: $(basename "$0") [--skip-docker]

Applies configs from the repository:

  deploy/daily-stats.service  -> /etc/systemd/system/daily-stats.service
  docker compose up -d --build
    with docker-compose.yml only, if TELEGRAM_PROXY_URL is empty
    with docker-compose.proxy.yml too, if TELEGRAM_PROXY_URL is set
      (external Docker network telegram-proxy; start mihomo-proxy first)

Does not install mihomo, does not write docker-compose.override.yml,
and does not change .env.

To update later: git pull --ff-only && ./deploy.sh

  --skip-docker   only install the systemd unit, do not touch the container
EOF
}

for arg in "$@"; do
  case "$arg" in
    --skip-docker) SKIP_DOCKER=1 ;;
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

need_cmd docker
need_cmd systemctl
if [[ "$(id -u)" -ne 0 ]] && ! command -v sudo >/dev/null 2>&1; then
  echo "error: need root or sudo to install the systemd unit" >&2
  exit 1
fi

if [[ ! -f "$ROOT/.env" ]]; then
  echo "==> creating .env from .env.example"
  cp "$ROOT/.env.example" "$ROOT/.env"
fi

bot_token="$(env_file_value BOT_TOKEN "$ROOT/.env")"
owner_id="$(env_file_value OWNER_TELEGRAM_ID "$ROOT/.env")"
if [[ -z "$bot_token" || "$bot_token" == 1234567890:* ]]; then
  echo "error: fill BOT_TOKEN in $ROOT/.env before deploy" >&2
  exit 1
fi
if [[ -z "$owner_id" || "$owner_id" == "123456789" ]]; then
  echo "error: fill OWNER_TELEGRAM_ID in $ROOT/.env before deploy" >&2
  exit 1
fi

proxy_url="$(env_file_value TELEGRAM_PROXY_URL "$ROOT/.env")"
mihomo_api_url="$(env_file_value MIHOMO_API_URL "$ROOT/.env")"
compose_extra=""
if [[ -n "$proxy_url" ]]; then
  compose_extra=" -f docker-compose.yml -f docker-compose.proxy.yml"
  echo "==> Telegram proxy: ${proxy_url}"
  if [[ "$proxy_url" == *127.0.0.1* ]]; then
    echo "warning: TELEGRAM_PROXY_URL points at 127.0.0.1; Docker should use socks5://proxy:11808" >&2
  fi
  if [[ "$mihomo_api_url" == *127.0.0.1* ]]; then
    echo "warning: MIHOMO_API_URL points at 127.0.0.1; Docker should use http://proxy:19090" >&2
  fi
else
  echo "==> Telegram proxy: direct (TELEGRAM_PROXY_URL empty)"
fi

echo "==> ensuring data/ and backups/"
mkdir -p "$ROOT/data" "$ROOT/backups"

if [[ -f "$ROOT/docker-compose.override.yml" ]] && grep -qE 'network_mode:[[:space:]]*host' "$ROOT/docker-compose.override.yml"; then
  echo "==> removing leftover docker-compose.override.yml (host network)"
  rm -f "$ROOT/docker-compose.override.yml"
fi

echo "==> git commit for image"
load_git_version
echo "    ${GIT_COMMIT} ${GIT_COMMIT_TITLE}"

echo "==> installing daily-stats.service (WorkingDirectory=$ROOT)"
docker_bin="$(command -v docker)"
unit_tmp="$(mktemp)"
awk -v wd="$ROOT" -v docker="$docker_bin" -v extra="$compose_extra" '
  /^WorkingDirectory=/ { print "WorkingDirectory=" wd; next }
  /^ExecStart=/ { print "ExecStart=" docker " compose" extra " up -d"; next }
  /^ExecStop=/ { print "ExecStop=" docker " compose" extra " stop"; next }
  { print }
' "$ROOT/deploy/daily-stats.service" > "$unit_tmp"
run_sudo install -m 644 "$unit_tmp" /etc/systemd/system/daily-stats.service
rm -f "$unit_tmp"

echo "==> removing leftover daily-stats-update timer"
run_sudo systemctl disable --now daily-stats-update.timer 2>/dev/null || true
run_sudo systemctl disable --now daily-stats-update.service 2>/dev/null || true
run_sudo rm -f /etc/systemd/system/daily-stats-update.timer /etc/systemd/system/daily-stats-update.service

echo "==> systemd daemon-reload"
run_sudo systemctl daemon-reload
run_sudo systemctl enable daily-stats.service

if [[ -n "$proxy_url" ]]; then
  docker network create telegram-proxy 2>/dev/null || true
  echo "    network telegram-proxy (external); start mihomo-proxy first if SOCKS/API fail"
fi

if [[ "$SKIP_DOCKER" -eq 1 ]]; then
  echo "==> skip docker"
else
  echo "==> docker compose${compose_extra} up -d --build"
  (
    cd "$ROOT"
    if [[ -n "$proxy_url" ]]; then
      docker compose -f docker-compose.yml -f docker-compose.proxy.yml up -d --build
    else
      docker compose up -d --build
    fi
  )
  run_sudo systemctl start daily-stats.service
fi

echo
echo "done"
if [[ -n "$proxy_url" ]]; then
  echo "  SOCKS/API    mihomo-proxy on Docker network telegram-proxy"
  echo "  proxy URL    ${proxy_url}"
  if [[ -n "$mihomo_api_url" ]]; then
    echo "  mihomo API   ${mihomo_api_url}"
  fi
else
  echo "  Telegram     direct (no proxy compose file)"
fi
if [[ "$SKIP_DOCKER" -eq 0 ]]; then
  echo "  container    $(docker inspect -f '{{.State.Status}}' daily-stats-bot 2>/dev/null || echo not-created)"
fi
echo
echo "check:"
echo "  docker compose -f \"$ROOT/docker-compose.yml\" logs -f bot"
if [[ -n "$proxy_url" ]]; then
  echo "  look for telegram_proxy_enabled"
  echo "  curl from network telegram-proxy (not localhost on the host), e.g.:"
  echo "  docker run --rm --network telegram-proxy curlimages/curl -sS --max-time 8 -x socks5h://proxy:11808 https://api.telegram.org"
fi
