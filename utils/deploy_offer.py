"""Host and bot share files in ./data so a git update waits for owner confirmation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

OFFER_NAME = ".deploy-offer"
DECISION_NAME = ".deploy-decision"

APPROVE = "approve"
SKIP = "skip"

DecisionAction = Literal["approve", "skip"]


@dataclass(frozen=True, slots=True)
class DeployOffer:
    was: str
    new: str
    was_short: str = ""
    was_title: str = ""
    new_short: str = ""
    new_title: str = ""
    notified: bool = False


@dataclass(frozen=True, slots=True)
class DeployDecision:
    action: DecisionAction
    sha: str


def offer_path(data_dir: Path) -> Path:
    return data_dir / OFFER_NAME


def decision_path(data_dir: Path) -> Path:
    return data_dir / DECISION_NAME


def _parse_fields(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    fields: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        fields[key] = value
    return fields


def _short(sha: str, stored: str) -> str:
    return stored or sha[:7]


def read_offer(data_dir: Path) -> DeployOffer | None:
    fields = _parse_fields(offer_path(data_dir))
    was = fields.get("was", "").strip()
    new = fields.get("new", "").strip()
    if not was or not new:
        return None
    return DeployOffer(
        was=was,
        new=new,
        was_short=_short(was, fields.get("was_short", "").strip()),
        was_title=fields.get("was_title", "").strip() or "unknown",
        new_short=_short(new, fields.get("new_short", "").strip()),
        new_title=fields.get("new_title", "").strip() or "unknown",
        notified=fields.get("notified", "").strip() == "1",
    )


def read_decision(data_dir: Path) -> DeployDecision | None:
    fields = _parse_fields(decision_path(data_dir))
    action = fields.get("action", "").strip()
    sha = fields.get("sha", "").strip()
    if action not in {APPROVE, SKIP} or not sha:
        return None
    return DeployDecision(action=action, sha=sha)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def write_decision(data_dir: Path, action: DecisionAction, sha: str) -> None:
    _atomic_write(decision_path(data_dir), f"action={action}\nsha={sha}\n")


def clear_deploy_handshake(data_dir: Path) -> None:
    offer_path(data_dir).unlink(missing_ok=True)
    decision_path(data_dir).unlink(missing_ok=True)
