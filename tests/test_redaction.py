from app.processing.redaction import redact_text


def test_approved_sensitive_content_still_redacted():
    value = ("password: UnitTestOnly123\napi_key: fixture-not-a-real-key\n"
             "4111 1111 1111 1111\n110101199003078888\n"
             "-----BEGIN PRIVATE KEY-----\nunit-test-material\n-----END PRIVATE KEY-----")
    result = redact_text(value)
    assert "UnitTestOnly123" not in result.text
    assert "fixture-not-a-real-key" not in result.text
    assert "4111" not in result.text
    assert "110101" not in result.text
    assert "unit-test-material" not in result.text
    assert result.count == 5


def test_redacts_email_and_international_phone_in_latin_and_persian_digits():
    result = redact_text(
        "Contact frank.fu@vertu.cn, +98 912 123 4567 or ۰۹۱۲ ۱۲۳ ۴۵۶۷. Year 2026 stays."
    )

    assert result.count == 3
    assert "frank.fu" not in result.text
    assert "912 123" not in result.text
    assert "۱۲۳" not in result.text
    assert "2026" in result.text


def test_redacts_email_at_sentence_end():
    result = redact_text("Contact frank.fu@vertu.cn.")

    assert result.text == "Contact [REDACTED_EMAIL]."


def test_preserves_valid_iso_date_while_redacting_phone():
    result = redact_text("Confirmed 2026-08-20, call +98 912 123 4567.")

    assert "2026-08-20" in result.text
    assert "+98 912" not in result.text
    assert result.count == 1
