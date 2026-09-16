from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


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
    fast = _mihomo_group(text, "FAST")
    backup = _mihomo_group(text, "BACKUP")
    assert "type: fallback" in auto
    assert "type: url-test" not in auto
    assert "- FAST" in auto
    assert "- BACKUP" in auto
    assert "include-all-providers:" not in auto
    assert "url: https://api.telegram.org/bot" in auto
    assert "expected-status: 404" in auto
    assert "max-failed-times: 1" in auto
    assert "type: url-test" in fast
    assert "tolerance:" in fast
    assert "timeout: 4000" in fast
    assert "timeout: 2500" not in fast
    assert "Anycast" in fast
    fast_use = fast.split("use:", 1)[1].split("url:", 1)[0]
    assert "- sub3" in fast_use
    assert "- sub1" not in fast_use
    assert "- sub2" not in fast_use
    assert "- sub4" not in fast_use
    assert "- sub5" not in fast_use
    assert "type: fallback" in backup
    assert "type: url-test" not in backup
    assert "lazy: true" in backup
    assert "timeout: 4000" in backup
    assert "max-failed-times: 1" in backup
    backup_use = backup.split("use:", 1)[1].split("url:", 1)[0]
    assert "- sub1" in backup_use
    assert "- sub2" in backup_use
    assert "- sub3" not in backup_use
    assert "- sub4" not in backup_use
    assert "- sub5" not in backup_use
    whitelist = _mihomo_group(text, "WHITELIST")
    assert "- sub4" in whitelist
    assert "- sub5" in whitelist
    assert "MATCH,AUTO" in text
    assert "MATCH,WHITELIST" not in text
    assert "interval: 300" in text
    assert text.count("lazy: true") >= 6
    assert "timeout: 4000" in auto
    assert "    proxy: AUTO\n" not in text
    assert text.count("    proxy: DIRECT") == 0
    assert text.count("    proxy: SUBSCRIBE") == 5
    subscribe = _mihomo_group(text, "SUBSCRIBE")
    assert "type: fallback" in subscribe
    assert "- DIRECT" in subscribe
    assert "- AUTO" in subscribe
    assert "api.telegram.org" not in subscribe
    assert subscribe.find("- DIRECT") < subscribe.find("- AUTO")


def test_deploy_scripts_are_valid_bash():
    path = REPO / "deploy.sh"
    subprocess.check_call(["bash", "-n", str(path)])
    mode = path.stat().st_mode
    assert mode & stat.S_IXUSR, f"{path} must be executable"


def test_load_git_version_skips_merge_commit_titles(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git is required")
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t.t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t.t",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
    }

    def git(*args: str) -> None:
        subprocess.check_call(["git", "-C", str(repo), *args], env=env)

    git("init", "-b", "main")
    git("config", "user.name", "t")
    git("config", "user.email", "t@t.t")
    git("config", "commit.gpgsign", "false")
    (repo / "a.txt").write_text("a", encoding="utf-8")
    git("add", "a.txt")
    git("commit", "-m", "real feature")
    git("checkout", "-b", "other")
    (repo / "b.txt").write_text("b", encoding="utf-8")
    git("add", "b.txt")
    git("commit", "-m", "side work")
    git("checkout", "main")
    git(
        "merge",
        "other",
        "--no-ff",
        "-m",
        "Merge branch 'main' of https://github.com/mamonaronov/daily-stats",
    )
    script = f"""
    ROOT="{repo}"
    # shellcheck source=deploy/lib.sh
    source "{REPO / "deploy" / "lib.sh"}"
    load_git_version
    printf '%s\\n' "$GIT_COMMIT_TITLE"
    """
    title = subprocess.check_output(["bash", "-c", script], text=True).strip()
    assert title == "real feature"
    assert "Merge" not in title
