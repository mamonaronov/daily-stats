from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "deploy" / "lib.sh"


def _bash(script: str, env: dict[str, str] | None = None) -> str:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    cmd = f"set -euo pipefail; ROOT={REPO.as_posix()!r}; source {LIB.as_posix()!r}; {script}"
    return subprocess.check_output(["bash", "-c", cmd], text=True, env=merged).rstrip("\n")


def test_html_escape_encodes_markup():
    assert _bash('html_escape "a<b>&c"') == "a&lt;b&gt;&amp;c"


def test_format_deploy_messages_include_commit_and_titles():
    start = _bash("format_deploy_start HEAD HEAD")
    assert start.startswith("🚀 <b>Деплой</b>")
    assert "без подтверждения" in start
    assert "бэкапа" in start
    assert "<code>" in start
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


def test_deploy_scripts_are_valid_bash():
    scripts = [
        REPO / "deploy" / "update.sh",
        REPO / "deploy.sh",
    ]
    for path in scripts:
        subprocess.check_call(["bash", "-n", str(path)])
        mode = path.stat().st_mode
        assert mode & stat.S_IXUSR, f"{path} must be executable"
