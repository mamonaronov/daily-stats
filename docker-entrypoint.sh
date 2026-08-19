#!/bin/sh
set -eu

# Bind mounts ./data and ./backups are often created as root on the host.
# The app runs as botuser (uid 1000); SQLite cannot create files otherwise.
if [ "$(id -u)" = "0" ]; then
  mkdir -p /app/data /app/backups
  chown -R botuser:botuser /app/data /app/backups
  # .env on the host is often 600; copy so the bot can pack the real file.
  if [ -f /host/.env ]; then
    cp /host/.env /app/.env.runtime
    chown botuser:botuser /app/.env.runtime
    chmod 400 /app/.env.runtime
  fi
  exec gosu botuser "$0" "$@"
fi

exec "$@"
