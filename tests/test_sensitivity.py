from app.processing.sensitivity import high_sensitivity_reasons


def test_detects_credentials_identity_and_payment_card():
    text = "password: SuperSecret123 API_KEY=sk-example-secret 11010519491231002X 4111 1111 1111 1111"

    reasons = high_sensitivity_reasons(text)

    assert reasons == ["api_key", "china_identity", "password", "payment_card"]


def test_does_not_quarantine_normal_business_contacts():
    text = "Contact frank.fu@vertu.cn or +98 912 123 4567 for the launch event."

    assert high_sensitivity_reasons(text, filename="launch-plan.pdf") == []


def test_restricted_or_sensitive_filename_is_quarantined():
    assert high_sensitivity_reasons("", filename="dealer-api_key.txt") == [
        "sensitive_filename"
    ]
    assert high_sensitivity_reasons("", sensitivity="restricted") == [
        "restricted_classification"
    ]
