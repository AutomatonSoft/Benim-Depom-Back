import pytest

from apps.ean.serializers import EanImportSerializer, is_valid_gtin


@pytest.mark.unit
@pytest.mark.parametrize(
    "code",
    ["4006381333931", "9501101530003", "12345670", "01234567890128"],
)
def test_valid_gtin_codes_are_accepted(code):
    assert is_valid_gtin(code) is True


@pytest.mark.unit
@pytest.mark.parametrize(
    "code",
    ["4006381333932", "not-a-code", "123", "123456789012345"],
)
def test_invalid_gtin_codes_are_rejected(code):
    assert is_valid_gtin(code) is False


@pytest.mark.unit
def test_ean_import_requires_non_empty_list():
    serializer = EanImportSerializer(data={"account": "jv", "codes": "\n  \n"})

    assert serializer.is_valid() is False
    assert "codes" in serializer.errors
