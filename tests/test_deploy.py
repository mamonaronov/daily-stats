from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "deploy" / "lib.sh"


def _bash(script: str, env: dict[str, str] | None = None, *, root: Path | None = None) -> str:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    repo = (root or REPO).as_posix()
    cmd = f"set -euo pipefail; ROOT={repo!r}; source {LIB.as_posix()!r}; {script}"
    return subprocess.check_output(["bash", "-c", cmd], text=True, env=merged).rstrip("\n")


def test_html_escape_encodes_markup():
    assert _bash('html_escape "a<b>&c"') == "a&lt;b&gt;&amp;c"


def test_format_deploy_messages_include_commit_and_titles():
    start = _bash("format_deploy_start HEAD HEAD")
    assert start.startswith("🚀 <b>Деплой</b>")
    assert "без подтверждения" not in start
    assert "Выкатываю новую версию." in start
    assert "бэкапа" in start
    assert "<code>" in start
    offer = _bash("format_deploy_offer HEAD HEAD")
    assert offer.startswith("📦 <b>Доступно обновление</b>")
    assert "Выкатить эту версию?" in offer
    done = _bash("format_deploy_done HEAD")
    assert done.startswith("✅ <b>Деплой завершён</b>")
    assert "Бот работает на" in done
    fail = _bash('format_deploy_fail "сборка" HEAD "line <1> & 2"')
    assert fail.startswith("🚨 <b>Деплой не удался</b>")
    assert "Этап: сборка" in fail
    assert "<pre>line &lt;1&gt; &amp; 2</pre>" in fail


def test_env_value_reads_dotenv(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("BOT_TOKEN=abc:def\nOWNER_TELEGRAM_ID=42\nTELEGRAM_PROXY_URL=socks5://127.0.0.1:11808\n")
    assert _bash(f"env_value BOT_TOKEN {env_file.as_posix()!r}") == "abc:def"
    assert _bash(f"env_value OWNER_TELEGRAM_ID {env_file.as_posix()!r}") == "42"
    proxy = _bash(
        f"ROOT={tmp_path.as_posix()!r}; source {LIB.as_posix()!r}; telegram_proxy_arg",
    )
    assert proxy == "socks5h://127.0.0.1:11808"


def _mihomo_group(text: str, name: str) -> str:
    marker = f"- name: {name}"
    rest = text.split("proxy-groups:", 1)[1]
    start = rest.index(marker)
    tail = rest[start + len(marker) :]
    nxt = tail.find("\n  - name:")
    return rest[start:] if nxt < 0 else rest[start : start + len(marker) + nxt]


def test_mihomo_auto_is_fallback_of_tunnel_providers():
    text = (REPO / "deploy" / "mihomo" / "config.yaml").read_text(encoding="utf-8")
    auto = _mihomo_group(text, "AUTO")
    assert "type: fallback" in auto
    assert "type: url-test" not in auto
    assert "include-all-providers:" not in auto
    assert "url: https://api.telegram.org/bot" in auto
    assert "expected-status: 404" in auto
    assert "max-failed-times: 1" in auto
    use = auto.split("use:", 1)[1].split("url:", 1)[0]
    assert "- sub3" in use
    assert "- sub1" in use
    assert "- sub2" in use
    assert "- sub4" not in use
    assert "- sub5" not in use
    whitelist = _mihomo_group(text, "WHITELIST")
    assert "- sub4" in whitelist
    assert "- sub5" in whitelist
    assert "MATCH,AUTO" in text
    assert "MATCH,WHITELIST" not in text


def test_deploy_scripts_are_valid_bash():
    scripts = [
        REPO / "deploy" / "update.sh",
        REPO / "deploy.sh",
    ]
    for path in scripts:
        subprocess.check_call(["bash", "-n", str(path)])
        mode = path.stat().st_mode
        assert mode & stat.S_IXUSR, f"{path} must be executable"


def _decide(tmp_path: Path, script: str) -> str:
    return _bash(script, root=tmp_path)


def test_decide_update_action_states(tmp_path: Path):
    (tmp_path / "data").mkdir()
    assert _decide(tmp_path, "FORCE=0 LOCAL=aaa REMOTE=aaa; decide_update_action") == "up_to_date"
    assert _decide(tmp_path, "FORCE=1 LOCAL=aaa REMOTE=aaa; decide_update_action") == "deploy"
    assert _decide(tmp_path, "FORCE=0 LOCAL=aaa REMOTE=bbb; decide_update_action") == "notify"

    _bash("write_deploy_offer aaa bbb; mark_deploy_offer_notified", root=tmp_path)
    assert _decide(tmp_path, "FORCE=0 LOCAL=aaa REMOTE=bbb; decide_update_action") == "waiting"

    (tmp_path / "data" / ".deploy-decision").write_text("action=skip\nsha=bbb\n", encoding="utf-8")
    assert _decide(tmp_path, "FORCE=0 LOCAL=aaa REMOTE=bbb; decide_update_action") == "skipped"

    (tmp_path / "data" / ".deploy-decision").write_text("action=approve\nsha=bbb\n", encoding="utf-8")
    assert _decide(tmp_path, "FORCE=0 LOCAL=aaa REMOTE=bbb; decide_update_action") == "deploy"

    assert _decide(tmp_path, "FORCE=0 LOCAL=aaa REMOTE=ccc; decide_update_action") == "notify"


def test_sync_deploy_offer_resets_stale_decision(tmp_path: Path):
    _bash("write_deploy_offer aaa bbb; mark_deploy_offer_notified", root=tmp_path)
    (tmp_path / "data" / ".deploy-decision").write_text("action=skip\nsha=bbb\n", encoding="utf-8")
    _bash("sync_deploy_offer aaa ccc", root=tmp_path)
    assert _bash("deploy_offer_field new", root=tmp_path) == "ccc"
    assert _bash("deploy_offer_field notified", root=tmp_path) == ""
    assert not (tmp_path / "data" / ".deploy-decision").exists()


def test_deploy_confirm_markup_matches_keyboard():
    from keyboards.main import admin_deploy_confirm_kb
    from utils.callbacks import ADMIN_DEPLOY_NO, ADMIN_DEPLOY_OK

    markup = _bash("deploy_confirm_markup")
    assert ADMIN_DEPLOY_OK in markup
    assert ADMIN_DEPLOY_NO in markup
    pairs = [
        (btn.text, btn.callback_data)
        for row in admin_deploy_confirm_kb().inline_keyboard
        for btn in row
    ]
    assert ("🔄 Обновить", ADMIN_DEPLOY_OK) in pairs
    assert ("⏭ Позже", ADMIN_DEPLOY_NO) in pairs
    assert all(data and len(data.encode()) <= 64 for _, data in pairs)


def test_admin_updates_kb_actions():
    from keyboards.main import admin_updates_kb
    from utils.callbacks import ADMIN_DEPLOY_NO, ADMIN_DEPLOY_OK, ADMIN_UPDATES, NAV_ADMIN

    idle = [
        (btn.text, btn.callback_data)
        for row in admin_updates_kb().inline_keyboard
        for btn in row
    ]
    assert ("🛠 Админка", NAV_ADMIN) in idle
    assert all(data != ADMIN_DEPLOY_OK and data != ADMIN_DEPLOY_NO for _, data in idle)

    pending = [
        (btn.text, btn.callback_data)
        for row in admin_updates_kb(can_approve=True, can_skip=True).inline_keyboard
        for btn in row
    ]
    assert ("🔄 Обновить", ADMIN_DEPLOY_OK) in pending
    assert ("⏭ Позже", ADMIN_DEPLOY_NO) in pending
    assert ("🛠 Админка", NAV_ADMIN) in pending

    skipped = [
        btn.callback_data
        for row in admin_updates_kb(can_approve=True).inline_keyboard
        for btn in row
    ]
    assert ADMIN_DEPLOY_OK in skipped
    assert ADMIN_DEPLOY_NO not in skipped
    assert ADMIN_UPDATES not in skipped
    assert all(data and len(data.encode()) <= 64 for data in skipped)
