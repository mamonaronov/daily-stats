# Shared helpers for deploy.sh and generate-docker-override.sh.
# Caller must set ROOT to the repository root.

yaml_scalar() {
  local file="$1" key="$2"
  awk -v k="$key" '
    $1 == k ":" {
      val = $2
      for (i = 3; i <= NF; i++) val = val " " $i
      gsub(/^"/, "", val)
      gsub(/"$/, "", val)
      print val
      exit
    }
  ' "$file"
}

load_mihomo_settings() {
  local cfg="${MIHOMO_CFG:-$ROOT/deploy/mihomo/config.yaml}"
  if [[ ! -f "$cfg" ]]; then
    echo "error: mihomo config not found: $cfg" >&2
    return 1
  fi
  MIXED_PORT="$(yaml_scalar "$cfg" mixed-port)"
  MIHOMO_CONTROLLER="$(yaml_scalar "$cfg" external-controller)"
  MIHOMO_SECRET="$(yaml_scalar "$cfg" secret)"
  if [[ -z "$MIXED_PORT" || -z "$MIHOMO_CONTROLLER" ]]; then
    echo "error: mixed-port / external-controller missing in $cfg" >&2
    return 1
  fi
  MIHOMO_API_URL="http://${MIHOMO_CONTROLLER}"
}

write_docker_override() {
  local out="${1:-$ROOT/docker-compose.override.yml}"
  cat > "$out" <<EOF
# Generated from deploy/mihomo/config.yaml — do not commit.
# Host network so 127.0.0.1 inside the container is the host's mihomo.
# Mixed ${MIXED_PORT} / API ${MIHOMO_CONTROLLER} — v2rayN keeps 10808/10809.
services:
  bot:
    network_mode: host
    environment:
      HTTP_PROXY: http://127.0.0.1:${MIXED_PORT}
      HTTPS_PROXY: http://127.0.0.1:${MIXED_PORT}
      NO_PROXY: localhost,127.0.0.1
      ALL_PROXY: socks5://127.0.0.1:${MIXED_PORT}
      TELEGRAM_PROXY_URL: socks5://127.0.0.1:${MIXED_PORT}
EOF
}

upsert_env() {
  local key="$1" value="$2" file="$3"
  local tmp
  tmp="$(mktemp)"
  awk -v k="$key" -v v="$value" '
    BEGIN { re = "^" k "=" }
    $0 ~ re { print k "=" v; found = 1; next }
    { print }
    END { if (!found) print k "=" v }
  ' "$file" > "$tmp"
  mv "$tmp" "$file"
}

port_listening() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -lnt 2>/dev/null | grep -qE ":${port}([^0-9]|$)"
    return $?
  fi
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
    return $?
  fi
  timeout 1 bash -c "echo >/dev/tcp/127.0.0.1/${port}" 2>/dev/null
}

wait_for_port() {
  local port="$1" seconds="${2:-30}" n=0
  while (( n < seconds )); do
    if port_listening "$port"; then
      return 0
    fi
    sleep 1
    n=$((n + 1))
  done
  return 1
}

run_sudo() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}
