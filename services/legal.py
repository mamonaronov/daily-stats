"""Privacy policy and terms of service shown inside the bot."""

from __future__ import annotations

import html
import re
from functools import lru_cache
from pathlib import Path

from config import PROJECT_ROOT

LEGAL_DIR = PROJECT_ROOT / "legal"
PRIVACY_PATH = LEGAL_DIR / "privacy.md"
TERMS_PATH = LEGAL_DIR / "terms.md"

DOC_PRIVACY = "privacy"
DOC_TERMS = "terms"
DOC_PATHS = {DOC_PRIVACY: PRIVACY_PATH, DOC_TERMS: TERMS_PATH}
DOC_TITLES = {
    DOC_PRIVACY: "Политика конфиденциальности",
    DOC_TERMS: "Пользовательское соглашение",
}

ORIGIN_CONSENT = "c"
ORIGIN_SETTINGS = "s"

PAGE_LIMIT = 3500
_HEADER_RE = re.compile(r"^(#{1,3})\s+(.*)$")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def legal_contact(owner_contact: str) -> str:
    return (owner_contact or "").strip() or "владелец сервиса"


@lru_cache(maxsize=4)
def _raw_markdown(path: str) -> str:
    return Path(path).read_text(encoding="utf-8").strip()


def _inline(text: str) -> str:
    parts: list[str] = []
    last = 0
    for match in _BOLD_RE.finditer(text):
        parts.append(html.escape(text[last : match.start()]))
        parts.append(f"<b>{html.escape(match.group(1))}</b>")
        last = match.end()
    parts.append(html.escape(text[last:]))
    return "".join(parts)


def markdown_to_telegram_html(source: str, *, contact: str) -> str:
    filled = source.replace("{contact}", contact)
    lines: list[str] = []
    for raw in filled.splitlines():
        header = _HEADER_RE.match(raw)
        if header:
            lines.append(f"<b>{html.escape(header.group(2).strip())}</b>")
            continue
        stripped = raw.strip()
        if stripped.startswith("- "):
            lines.append(f"• {_inline(stripped[2:])}")
            continue
        lines.append(_inline(raw))
    return "\n".join(lines).strip()


def paginate_html(text: str, limit: int = PAGE_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]
    pages: list[str] = []
    chunks = text.split("\n\n")
    current: list[str] = []
    size = 0
    for chunk in chunks:
        extra = len(chunk) + (2 if current else 0)
        if current and size + extra > limit:
            pages.append("\n\n".join(current))
            current = []
            size = 0
            extra = len(chunk)
        if extra > limit:
            if current:
                pages.append("\n\n".join(current))
                current = []
                size = 0
            pages.extend(_split_oversize(chunk, limit))
            continue
        current.append(chunk)
        size += extra
    if current:
        pages.append("\n\n".join(current))
    return pages or [text]


def _split_oversize(chunk: str, limit: int) -> list[str]:
    pages: list[str] = []
    rest = chunk
    while rest:
        if len(rest) <= limit:
            pages.append(rest)
            break
        cut = rest.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = rest.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        pages.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    return pages


def document_pages(doc: str, contact: str) -> list[str]:
    if doc not in DOC_PATHS:
        raise KeyError(doc)
    html_text = markdown_to_telegram_html(_raw_markdown(str(DOC_PATHS[doc])), contact=contact)
    return paginate_html(html_text)


def document_page(doc: str, page: int, contact: str) -> tuple[str, int, int]:
    pages = document_pages(doc, contact)
    index = max(0, min(page, len(pages) - 1))
    return pages[index], index, len(pages)
