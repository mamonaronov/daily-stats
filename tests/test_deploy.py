from __future__ import annotations

import stat
import subprocess
from pathlib import Path

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
    path = REPO / "deploy.sh"
    subprocess.check_call(["bash", "-n", str(path)])
    mode = path.stat().st_mode
    assert mode & stat.S_IXUSR, f"{path} must be executable"
