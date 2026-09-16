import pytest

from apps.common.contact import InvalidContactPhone, normalize_contact_phone


@pytest.mark.unit
def test_normalizes_international_whatsapp_number():
    assert normalize_contact_phone("+49 176 43450100") == "+4917643450100"
    assert normalize_contact_phone("0049-176-43450100") == "+4917643450100"


@pytest.mark.unit
def test_rejects_local_or_invalid_numbers():
    with pytest.raises(InvalidContactPhone):
        normalize_contact_phone("0546505530")
    with pytest.raises(InvalidContactPhone):
        normalize_contact_phone("abc")
    assert normalize_contact_phone("  ") == ""
