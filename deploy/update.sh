#!/usr/bin/env bash
# Pull origin/main (if needed), ask the owner in Telegram, rebuild the bot container.
# Installed as a systemd timer by ./deploy.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=deploy/lib.sh
source "$ROOT/deploy/lib.sh"

FORCE=0
STAGE="старт"
WAS_REV=""
NEW_REV=""
LOG=""

usage() {
  cat <<EOF
Usage: $(basename "$0") [--force]

    Fetches origin/main. When HEAD moved, asks the owner in the bot to confirm
    before rebuild. After approval waits until the running bot is idle, then
    switches the container. Notifies the owner before, after, and on failure.

  --force   rebuild without waiting for confirmation (also if HEAD already
            matches origin/main)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) FORCE=1 ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
  shift
done

LOCK="$ROOT/.deploy.lock"
exec 9>"$LOCK"
flock 9

LOG="$(mktemp)"
cleanup() {
  rm -f "$LOG"
}
trap cleanup EXIT

fail_notify() {
  local rc=$?
  trap - ERR
  if [[ "$rc" -eq 0 ]]; then
    return 0
  fi
  local tail=""
  if [[ -n "$LOG" && -f "$LOG" ]]; then
    tail="$(tail -c 1500 "$LOG" | tr -d '\000' || true)"
  fi
  notify_telegram "$(format_deploy_fail "$STAGE" "${NEW_REV:-$WAS_REV}" "$tail")"
  exit "$rc"
}
trap fail_notify ERR

container_running() {
  docker inspect -f '{{.State.Running}}' daily-stats-bot 2>/dev/null | grep -qx true
}

wait_for_bot_idle() {
  local dir="$ROOT/data"
  local request="$dir/.deploy-drain"
  local idle="$dir/.deploy-idle"
  local status="$dir/.deploy-status"
  mkdir -p "$dir"
  if ! container_running; then
    echo "bot container is not running, skip idle wait"
    return 0
  fi
  rm -f "$idle"
  printf '1\n' >"$request"
  local handshake=$((SECONDS + 15))
  while (( SECONDS < handshake )); do
    if [[ -f "$status" ]]; then
      break
    fi
    sleep 1
  done
  if [[ ! -f "$status" ]]; then
    echo "bot does not support drain yet, continue"
    rm -f "$request"
    return 0
  fi
  local deadline=$((SECONDS + 240))
  while (( SECONDS < deadline )); do
    if [[ -f "$idle" ]]; then
      echo "bot is idle"
      return 0
    fi
    if [[ -f "$status" ]]; then
      echo "waiting: $(tr -d '\n' <"$status")"
    fi
    sleep 1
  done
  echo "error: bot did not become idle in time" >&2
  if [[ -f "$status" ]]; then
    cat "$status" >>"$LOG" || true
  fi
  rm -f "$request"
  return 1
}

offer_update() {
  STAGE="уведомление о обновлении"
  sync_deploy_offer "$LOCAL" "$REMOTE"
  if [[ "$(deploy_offer_field notified)" == "1" ]]; then
    echo "waiting for owner confirmation $(git_short "$REMOTE")"
    return 0
  fi
  echo "offering update $(git_short "$LOCAL") -> $(git_short "$REMOTE")"
  notify_telegram "$(format_deploy_offer "$LOCAL" "$REMOTE")" "$(deploy_confirm_markup)"
  if [[ "${TELEGRAM_LAST_HTTP:-}" == "200" ]]; then
    mark_deploy_offer_notified
  else
    echo "warning: offer notify failed, will retry" >&2
  fi
}

cd "$ROOT"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  STAGE="проверка git"
  echo "error: $ROOT is not a git checkout" >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
  echo "docker is not ready, skip update" >&2
  trap - ERR
  exit 0
fi

STAGE="git fetch"
git fetch origin

LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse origin/main)"
WAS_REV="$LOCAL"
NEW_REV="$REMOTE"

ACTION="$(decide_update_action)"
case "$ACTION" in
  up_to_date)
    clear_deploy_handshake
    echo "already up to date $(git_short HEAD)"
    trap - ERR
    exit 0
    ;;
  skipped)
    echo "owner skipped $(git_short "$REMOTE")"
    trap - ERR
    exit 0
    ;;
  waiting)
    echo "waiting for owner confirmation $(git_short "$REMOTE")"
    trap - ERR
    exit 0
    ;;
  notify)
    offer_update
    trap - ERR
    exit 0
    ;;
  deploy)
    ;;
  *)
    echo "error: unknown update action: $ACTION" >&2
    exit 1
    ;;
esac

STAGE="уведомление перед деплоем"
notify_telegram "$(format_deploy_start "$WAS_REV" "$NEW_REV")"

if [[ "$LOCAL" != "$REMOTE" ]]; then
  STAGE="git merge"
  git merge --ff-only origin/main
fi

NEW_REV="$(git rev-parse HEAD)"
load_git_version
echo "==> building ${GIT_COMMIT} ${GIT_COMMIT_TITLE}"

STAGE="сборка образа"
set -o pipefail
docker compose build 2>&1 | tee -a "$LOG"
set +o pipefail

STAGE="ожидание простоя бота"
wait_for_bot_idle

STAGE="запуск новой версии"
set -o pipefail
docker compose up -d 2>&1 | tee -a "$LOG"
set +o pipefail

STAGE="ожидание готовности бота"
ready_deadline=$((SECONDS + 180))
while (( SECONDS < ready_deadline )); do
  if docker inspect -f '{{.State.Running}}' daily-stats-bot 2>/dev/null | grep -qx true; then
    if docker logs --since 10m daily-stats-bot 2>&1 | grep -qF 'Polling started'; then
      STAGE="уведомление после деплоя"
      notify_telegram "$(format_deploy_done "$NEW_REV")"
      clear_deploy_handshake
      echo "deployed $(git_short HEAD)"
      trap - ERR
      exit 0
    fi
  fi
  status="$(docker inspect -f '{{.State.Status}} {{.State.ExitCode}}' daily-stats-bot 2>/dev/null || echo missing)"
  echo "waiting for bot ($status)" >>"$LOG"
  sleep 2
done

echo "bot did not become ready (no Polling started)" >>"$LOG"
docker compose logs --tail 80 bot >>"$LOG" 2>&1 || true
false
