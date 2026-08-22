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

load_git_version() {
  if command -v git >/dev/null 2>&1 && git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    GIT_COMMIT="$(git -C "$ROOT" rev-parse --short HEAD)"
    GIT_COMMIT_TITLE="$(git -C "$ROOT" log -1 --pretty=%s)"
  else
    GIT_COMMIT="unknown"
    GIT_COMMIT_TITLE="unknown"
  fi
  export GIT_COMMIT GIT_COMMIT_TITLE
}

write_docker_override() {
  local out="${1:-$ROOT/docker-compose.override.yml}"
  cat > "$out" <<EOF
# Generated from deploy/mihomo/config.yaml — do not commit.
# Host network so 127.0.0.1 inside the container is the host's mihomo.
# Mixed ${MIXED_PORT} / API ${MIHOMO_CONTROLLER} — v2rayN keeps 10808/10809.
# Only TELEGRAM_PROXY_URL: HTTP_PROXY/ALL_PROXY can double-proxy aiohttp.
services:
  bot:
    network_mode: host
    environment:
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

wait_for_telegram_via_socks() {
  local port="$1" seconds="${2:-90}"
  local url="https://api.telegram.org"
  local deadline=$((SECONDS + seconds))
  while (( SECONDS < deadline )); do
    if curl -sS -o /dev/null --max-time 5 -x "socks5h://127.0.0.1:${port}" "$url" 2>/dev/null \
      || curl -sS -o /dev/null --max-time 5 -x "socks5://127.0.0.1:${port}" "$url" 2>/dev/null; then
      return 0
    fi
    sleep 2
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

html_escape() {
  local s="${1-}"
  # bash treats & in ${var/pat/repl} as the matched text; backslash keeps a literal &.
  s="${s//&/\&amp;}"
  s="${s//</\&lt;}"
  s="${s//>/\&gt;}"
  s="${s//\"/\&quot;}"
  printf '%s' "$s"
}

env_value() {
  local key="$1"
  local file="${2:-$ROOT/.env}"
  [[ -f "$file" ]] || return 0
  awk -F= -v k="$key" '
    $1 == k {
      val = substr($0, index($0, "=") + 1)
      sub(/\r$/, "", val)
      if (val ~ /^".*"$/) val = substr(val, 2, length(val) - 2)
      else if (val ~ /^\047.*\047$/) val = substr(val, 2, length(val) - 2)
      printf "%s", val
      exit
    }
  ' "$file"
}

telegram_proxy_arg() {
  local url=""
  url="$(env_value TELEGRAM_PROXY_URL || true)"
  if [[ -z "$url" && -f "${MIHOMO_CFG:-$ROOT/deploy/mihomo/config.yaml}" ]]; then
    if [[ -z "${MIXED_PORT:-}" ]]; then
      load_mihomo_settings >/dev/null 2>&1 || true
    fi
    if [[ -n "${MIXED_PORT:-}" ]]; then
      url="socks5h://127.0.0.1:${MIXED_PORT}"
    fi
  fi
  if [[ "$url" == socks5://* ]]; then
    url="socks5h://${url#socks5://}"
  fi
  printf '%s' "$url"
}

git_short() {
  git -C "$ROOT" rev-parse --short "$1" 2>/dev/null || printf '%s' "$1"
}

git_title() {
  git -C "$ROOT" log -1 --pretty=%s "$1" 2>/dev/null || printf '%s' "unknown"
}

format_commit_html() {
  local rev="$1"
  printf '<code>%s</code> — %s' "$(html_escape "$(git_short "$rev")")" "$(html_escape "$(git_title "$rev")")"
}

format_deploy_offer() {
  local was="$1" new="$2"
  printf '%s\n' "📦 <b>Доступно обновление</b>"
  printf '%s\n' "В репозитории новый коммит. Выкатить эту версию?"
  printf '\n'
  printf '%s\n' "Сейчас: $(format_commit_html "$was")"
  printf '%s\n' "Новая: $(format_commit_html "$new")"
}

format_deploy_start() {
  local was="$1" new="$2"
  printf '%s\n' "🚀 <b>Деплой</b>"
  printf '%s\n' "Выкатываю новую версию."
  printf '%s\n' "Сначала дождусь, пока не будет записей в базу, бэкапа и других операций."
  printf '\n'
  printf '%s\n' "Сейчас: $(format_commit_html "$was")"
  printf '%s\n' "Новая: $(format_commit_html "$new")"
}

deploy_confirm_markup() {
  printf '%s' '{"inline_keyboard":[[{"text":"🔄 Обновить","callback_data":"ad:upd:ok"},{"text":"⏭ Позже","callback_data":"ad:upd:no"}]]}'
}

deploy_offer_path() {
  printf '%s' "$ROOT/data/.deploy-offer"
}

deploy_decision_path() {
  printf '%s' "$ROOT/data/.deploy-decision"
}

deploy_file_field() {
  local key="$1"
  local path="$2"
  [[ -f "$path" ]] || return 0
  awk -F= -v k="$key" '
    $1 == k {
      print substr($0, index($0, "=") + 1)
      exit
    }
  ' "$path"
}

deploy_offer_field() {
  deploy_file_field "$1" "$(deploy_offer_path)"
}

deploy_decision_field() {
  deploy_file_field "$1" "$(deploy_decision_path)"
}

_one_line() {
  local s="${1-}"
  s="${s//$'\r'/ }"
  s="${s//$'\n'/ }"
  printf '%s' "$s"
}

write_deploy_offer() {
  local was="$1" new="$2"
  local path tmp
  mkdir -p "$ROOT/data"
  path="$(deploy_offer_path)"
  tmp="$(mktemp "${path}.XXXXXX")"
  {
    printf 'was=%s\n' "$was"
    printf 'new=%s\n' "$new"
    printf 'was_short=%s\n' "$(_one_line "$(git_short "$was")")"
    printf 'was_title=%s\n' "$(_one_line "$(git_title "$was")")"
    printf 'new_short=%s\n' "$(_one_line "$(git_short "$new")")"
    printf 'new_title=%s\n' "$(_one_line "$(git_title "$new")")"
  } >"$tmp"
  mv "$tmp" "$path"
}

mark_deploy_offer_notified() {
  local path tmp was new
  path="$(deploy_offer_path)"
  [[ -f "$path" ]] || return 0
  was="$(deploy_offer_field was)"
  new="$(deploy_offer_field new)"
  [[ -n "$was" && -n "$new" ]] || return 0
  tmp="$(mktemp "${path}.XXXXXX")"
  {
    printf 'was=%s\n' "$was"
    printf 'new=%s\n' "$new"
    printf 'was_short=%s\n' "$(deploy_offer_field was_short)"
    printf 'was_title=%s\n' "$(deploy_offer_field was_title)"
    printf 'new_short=%s\n' "$(deploy_offer_field new_short)"
    printf 'new_title=%s\n' "$(deploy_offer_field new_title)"
    printf 'notified=1\n'
  } >"$tmp"
  mv "$tmp" "$path"
}

clear_deploy_handshake() {
  rm -f "$(deploy_offer_path)" "$(deploy_decision_path)"
}

sync_deploy_offer() {
  local was="$1" new="$2"
  if [[ "$(deploy_offer_field new)" != "$new" ]]; then
    write_deploy_offer "$was" "$new"
    rm -f "$(deploy_decision_path)"
  fi
}

# FORCE, LOCAL, REMOTE must be set. Prints: up_to_date | skipped | waiting | notify | deploy
decide_update_action() {
  local offered_new decision_action decision_sha
  if [[ "$LOCAL" == "$REMOTE" && "${FORCE:-0}" -eq 0 ]]; then
    printf '%s\n' "up_to_date"
    return 0
  fi
  if [[ "${FORCE:-0}" -eq 1 ]]; then
    printf '%s\n' "deploy"
    return 0
  fi
  offered_new="$(deploy_offer_field new)"
  if [[ "$offered_new" != "$REMOTE" ]]; then
    printf '%s\n' "notify"
    return 0
  fi
  decision_action="$(deploy_decision_field action)"
  decision_sha="$(deploy_decision_field sha)"
  if [[ "$decision_sha" == "$REMOTE" && "$decision_action" == "skip" ]]; then
    printf '%s\n' "skipped"
    return 0
  fi
  if [[ "$decision_sha" == "$REMOTE" && "$decision_action" == "approve" ]]; then
    printf '%s\n' "deploy"
    return 0
  fi
  if [[ "$(deploy_offer_field notified)" == "1" ]]; then
    printf '%s\n' "waiting"
    return 0
  fi
  printf '%s\n' "notify"
}

format_deploy_done() {
  local rev="$1"
  printf '%s\n' "✅ <b>Деплой завершён</b>"
  printf '%s\n' "Бот работает на $(format_commit_html "$rev")."
}

format_deploy_fail() {
  local stage="$1" rev="$2" log="${3-}"
  printf '%s\n' "🚨 <b>Деплой не удался</b>"
  printf '%s\n' "Этап: $(html_escape "$stage")"
  if [[ -n "$rev" ]]; then
    printf '%s\n' "Коммит: $(format_commit_html "$rev")"
  fi
  if [[ -n "$log" ]]; then
    printf '\n<pre>%s</pre>\n' "$(html_escape "$log")"
  fi
}

notify_telegram() {
  local text="$1"
  local markup="${2-}"
  local token owner proxy url code tmp curl_args
  TELEGRAM_LAST_HTTP=""
  token="$(env_value BOT_TOKEN || true)"
  owner="$(env_value OWNER_TELEGRAM_ID || true)"
  if [[ -z "$token" || -z "$owner" ]]; then
    echo "warning: BOT_TOKEN / OWNER_TELEGRAM_ID missing, skip notify" >&2
    return 0
  fi
  tmp="$(mktemp)"
  url="https://api.telegram.org/bot${token}/sendMessage"
  curl_args=( -sS --max-time 20 -o "$tmp" -w "%{http_code}" )
  _telegram_post() {
    local proxy="${1-}"
    local args=( "${curl_args[@]}" )
    if [[ -n "$proxy" ]]; then
      args+=( -x "$proxy" )
    fi
    args+=(
      --data-urlencode "chat_id=${owner}"
      --data-urlencode "text=${text}"
      --data-urlencode "parse_mode=HTML"
      --data-urlencode "disable_web_page_preview=true"
    )
    if [[ -n "$markup" ]]; then
      args+=( --data-urlencode "reply_markup=${markup}" )
    fi
    args+=( "$url" )
    curl "${args[@]}" || true
  }
  proxy="$(telegram_proxy_arg || true)"
  code=""
  if [[ -n "$proxy" ]]; then
    code="$(_telegram_post "$proxy")"
  fi
  if [[ "$code" != "200" ]]; then
    code="$(_telegram_post "")"
  fi
  TELEGRAM_LAST_HTTP="$code"
  if [[ "$code" != "200" ]]; then
    echo "warning: telegram notify HTTP ${code:-failed}" >&2
  fi
  rm -f "$tmp"
  return 0
}
