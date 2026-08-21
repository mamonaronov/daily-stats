#!/usr/bin/env bash
# Restore this repo from a Telegram backup archive (daily-stats-backup_*.tar.gz)
# and optionally start the bot. Does not require Python on the host.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
START=0
KEEP_ENV=0
ARCHIVE=""

usage() {
  cat <<EOF
Usage: $(basename "$0") ARCHIVE [--start] [--keep-env]

Распаковывает бэкап, который бот присылает в Telegram:

  data/database.sqlite3   текущий файл уходит в data/*.pre_restore.*
  .env                    текущий копируется в .env.pre_restore.* (если нет --keep-env)
  restored-configs/       конфиги из архива, только для просмотра

  --start     docker compose up -d --build после восстановления
  --keep-env  не трогать существующий .env

Новый сервер:

  git clone <repo> && cd daily-stats
  ./restore.sh ~/Downloads/daily-stats-backup_….tar.gz
  ./deploy.sh          # mihomo + контейнер
  # или, если Telegram доступен напрямую:
  ./restore.sh ~/Downloads/daily-stats-backup_….tar.gz --start

Если бот уже запущен, скрипт сначала остановит контейнер.
EOF
}

for arg in "$@"; do
  case "$arg" in
    --start) START=1 ;;
    --keep-env) KEEP_ENV=1 ;;
    -h|--help)
      usage
      exit 0
      ;;
    --*)
      echo "error: unknown argument: $arg" >&2
      usage >&2
      exit 1
      ;;
    *)
      if [[ -n "$ARCHIVE" ]]; then
        echo "error: extra argument: $arg" >&2
        usage >&2
        exit 1
      fi
      ARCHIVE="$arg"
      ;;
  esac
done

if [[ -z "$ARCHIVE" ]]; then
  usage >&2
  exit 1
fi

if [[ ! -f "$ARCHIVE" ]]; then
  echo "error: archive not found: $ARCHIVE" >&2
  exit 1
fi

ARCHIVE="$(cd "$(dirname "$ARCHIVE")" && pwd)/$(basename "$ARCHIVE")"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "error: '$1' not found" >&2
    exit 1
  }
}

need_cmd tar
need_cmd gzip

tmp="$(mktemp -d "${TMPDIR:-/tmp}/daily-stats-restore.XXXXXX")"
cleanup() {
  rm -rf "$tmp"
}
trap cleanup EXIT

echo "==> extracting $(basename "$ARCHIVE")"
tar -xzf "$ARCHIVE" -C "$tmp"

db=""
if [[ -f "$tmp/database.sqlite3" ]]; then
  db="$tmp/database.sqlite3"
else
  db="$(find "$tmp" -maxdepth 2 -type f -name 'database.sqlite3' | head -n 1 || true)"
fi
if [[ -z "$db" || ! -f "$db" ]]; then
  echo "error: archive has no database.sqlite3 — not a bot backup" >&2
  exit 1
fi

header="$(head -c 15 "$db" || true)"
if [[ "$header" != "SQLite format 3" ]]; then
  echo "error: database.sqlite3 is not a SQLite file" >&2
  exit 1
fi

mkdir -p "$ROOT/data" "$ROOT/backups"
stamp="$(date -u +%Y%m%d_%H%M%S)"

if command -v docker >/dev/null 2>&1; then
  if docker compose -f "$ROOT/docker-compose.yml" ps --status running -q bot 2>/dev/null | grep -q .; then
    echo "==> stopping running bot"
    (cd "$ROOT" && docker compose stop bot)
  fi
fi

if [[ -f "$ROOT/data/database.sqlite3" ]]; then
  echo "==> quarantining current database"
  mv "$ROOT/data/database.sqlite3" "$ROOT/data/database.sqlite3.pre_restore.$stamp"
  rm -f "$ROOT/data/database.sqlite3-wal" "$ROOT/data/database.sqlite3-shm"
fi
cp "$db" "$ROOT/data/database.sqlite3"
echo "    data/database.sqlite3"

if [[ "$KEEP_ENV" -eq 0 && -f "$tmp/.env" ]]; then
  if [[ -f "$ROOT/.env" ]]; then
    cp "$ROOT/.env" "$ROOT/.env.pre_restore.$stamp"
    echo "    saved current .env as .env.pre_restore.$stamp"
  fi
  cp "$tmp/.env" "$ROOT/.env"
  chmod 600 "$ROOT/.env" || true
  echo "    .env from backup"
elif [[ "$KEEP_ENV" -eq 1 ]]; then
  echo "    keeping existing .env"
elif [[ ! -f "$ROOT/.env" ]]; then
  echo "warning: archive has no .env and $ROOT/.env is missing" >&2
  echo "warning: copy .env.example to .env and fill BOT_TOKEN / OWNER_TELEGRAM_ID" >&2
fi

if [[ -d "$tmp/configs" ]]; then
  rm -rf "$ROOT/restored-configs"
  cp -a "$tmp/configs" "$ROOT/restored-configs"
  echo "    restored-configs/ (review only, not applied)"
fi

echo
echo "restore complete"
echo "  database  $ROOT/data/database.sqlite3"
if [[ -f "$ROOT/.env" ]]; then
  echo "  env       $ROOT/.env"
fi

if [[ "$START" -eq 1 ]]; then
  need_cmd docker
  if [[ ! -f "$ROOT/.env" ]]; then
    echo "error: cannot start without .env" >&2
    exit 1
  fi
  echo "==> docker compose up -d --build"
  (cd "$ROOT" && docker compose up -d --build)
  echo
  echo "logs: docker compose -f \"$ROOT/docker-compose.yml\" logs -f bot"
else
  echo
  echo "next:"
  echo "  ./deploy.sh                 # новый сервер (mihomo + контейнер)"
  echo "  docker compose up -d --build"
fi
