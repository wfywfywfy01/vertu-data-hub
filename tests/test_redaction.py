from app.processing.redaction import redact_text


def test_redacts_email_and_international_phone_in_latin_and_persian_digits():
    result = redact_text(
        "Contact frank.fu@vertu.cn, +98 912 123 4567 or ۰۹۱۲ ۱۲۳ ۴۵۶۷. Year 2026 stays."
    )

    assert result.count == 3
    assert "frank.fu" not in result.text
    assert "912 123" not in result.text
    assert "۱۲۳" not in result.text
    assert "2026" in result.text
