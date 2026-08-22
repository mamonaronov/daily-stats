from __future__ import annotations

from pathlib import Path

from handlers.admin_deploy import format_deploy_accepted, format_deploy_skipped
from tests.test_deploy import _bash
from utils.deploy_offer import (
    APPROVE,
    SKIP,
    DeployOffer,
    clear_deploy_handshake,
    read_decision,
    read_offer,
    write_decision,
)


def _offer(**kwargs) -> DeployOffer:
    fields = dict(
        was="aaa111",
        new="bbb222",
        was_short="aaa111",
        was_title="old",
        new_short="bbb222",
        new_title="new",
    )
    fields.update(kwargs)
    return DeployOffer(**fields)


def test_read_offer_from_bash_file(tmp_path: Path):
    _bash("write_deploy_offer aaa111 bbb222; mark_deploy_offer_notified", root=tmp_path)
    offer = read_offer(tmp_path / "data")
    assert offer is not None
    assert offer.was == "aaa111"
    assert offer.new == "bbb222"
    assert offer.was_short == "aaa111"
    assert offer.was_title == "unknown"
    assert offer.notified is True


def test_bash_reads_python_decision(tmp_path: Path):
    data = tmp_path / "data"
    write_decision(data, APPROVE, "bbb222")
    assert _bash("deploy_decision_field action", root=tmp_path) == "approve"
    assert _bash("deploy_decision_field sha", root=tmp_path) == "bbb222"
    parsed = read_decision(data)
    assert parsed is not None
    assert parsed.action == APPROVE
    assert parsed.sha == "bbb222"


def test_write_decision_skip_roundtrip(tmp_path: Path):
    data = tmp_path / "data"
    write_decision(data, SKIP, "ccc")
    parsed = read_decision(data)
    assert parsed == read_decision(data)
    assert parsed is not None
    assert parsed.action == SKIP
    assert parsed.sha == "ccc"


def test_clear_deploy_handshake(tmp_path: Path):
    data = tmp_path / "data"
    _bash("write_deploy_offer aaa bbb", root=tmp_path)
    write_decision(data, APPROVE, "bbb")
    clear_deploy_handshake(data)
    assert read_offer(data) is None
    assert read_decision(data) is None


def test_read_offer_missing(tmp_path: Path):
    assert read_offer(tmp_path) is None
    assert read_decision(tmp_path) is None


def test_format_deploy_messages_escape_html():
    offer = _offer(was_title="x<y>", new_title="a&b")
    accepted = format_deploy_accepted(offer)
    assert accepted.startswith("✅ <b>Обновление принято</b>")
    assert "x&lt;y&gt;" in accepted
    assert "a&amp;b" in accepted
    skipped = format_deploy_skipped(offer)
    assert skipped.startswith("⏭ <b>Обновление отложено</b>")
    assert "x&lt;y&gt;" in skipped
