from __future__ import annotations

from services.legal import (
    DOC_PRIVACY,
    DOC_TERMS,
    PAGE_LIMIT,
    document_page,
    document_pages,
    legal_contact,
    markdown_to_telegram_html,
    paginate_html,
)


def test_legal_contact_fallback():
    assert legal_contact(" @owner ") == "@owner"
    assert legal_contact("") == "владелец сервиса"
    assert legal_contact("   ") == "владелец сервиса"


def test_markdown_escapes_and_substitutes_contact():
    html_text = markdown_to_telegram_html(
        "# Title\n\nHello <user> & {contact}\n\n- **bold** item",
        contact="@owner",
    )
    assert "<b>Title</b>" in html_text
    assert "Hello &lt;user&gt; &amp; @owner" in html_text
    assert "• <b>bold</b> item" in html_text
    assert "<user>" not in html_text


def test_documents_load_and_fit_telegram():
    contact = "@owner"
    for doc in (DOC_PRIVACY, DOC_TERMS):
        pages = document_pages(doc, contact)
        assert pages
        joined = "\n".join(pages)
        assert "@owner" in joined
        assert "{contact}" not in joined
        for page in pages:
            assert len(page) <= PAGE_LIMIT
            body, index, total = document_page(doc, 0 if page == pages[0] else 99, contact)
            assert total == len(pages)
            assert 0 <= index < total
            assert body


def test_privacy_covers_actual_processing():
    text = "\n".join(document_pages(DOC_PRIVACY, "@owner"))
    for needle in (
        "идентификатор аккаунта Telegram",
        "дневник",
        "резервн",
        "физически не стираются",
        "152-ФЗ",
        "@owner",
    ):
        assert needle in text


def test_terms_cover_payment_and_not_medical():
    text = "\n".join(document_pages(DOC_TERMS, "@owner"))
    lowered = text.lower()
    for needle in (
        "не медицинск",
        "баланс",
        "вне бота",
        "корреляц",
        "@owner",
    ):
        assert needle.lower() in lowered


def test_paginate_splits_long_text():
    chunk = "абзац " * 200
    text = f"{chunk}\n\n{chunk}\n\n{chunk}"
    pages = paginate_html(text, limit=400)
    assert len(pages) > 1
    assert all(len(page) <= 400 for page in pages)
    assert "".join(page.replace("\n\n", "") for page in pages).count("абзац") == text.count("абзац")
