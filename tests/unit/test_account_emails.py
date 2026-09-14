import pytest

from apps.accounts.emails import build_email_verification_message


@pytest.mark.unit
def test_verification_email_is_bilingual_turkish_and_english(settings):
    settings.EMAIL_VERIFICATION_CODE_TTL_MINUTES = 10
    subject, text_body, html_body = build_email_verification_message(code="482913")

    assert "Doğrulama kodunuz" in subject
    assert "Your verification code" in subject
    assert "Подтверждение" not in subject
    assert "482913" in text_body
    assert "482913" in html_body
    assert "Kayıt doğrulama" in html_body
    assert "Registration verification" in html_body
    assert "10 dakika" in html_body
    assert "10 minutes" in html_body
    assert "Ваш код" not in text_body
    assert "Ваш код" not in html_body
