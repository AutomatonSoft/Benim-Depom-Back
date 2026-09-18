import pytest

from apps.common.contact import contact_json_path


@pytest.fixture
def contact_file(tmp_path, settings):
    path = tmp_path / "contact.json"
    settings.CONTACT_JSON_PATH = path
    return path


@pytest.mark.django_db
def test_anonymous_can_read_whatsapp_contact(api_client, contact_file):
    contact_file.write_text('{"phone": "+4917643450100"}\n', encoding="utf-8")

    response = api_client.get("/api/v1/contact/whatsapp/")

    assert response.status_code == 200
    assert response.data == {
        "phone": "+4917643450100",
        "whatsapp_url": "https://wa.me/4917643450100",
    }


@pytest.mark.django_db
def test_manager_can_update_whatsapp_contact(api_client, manager, contact_file):
    api_client.force_authenticate(manager)

    response = api_client.patch(
        "/api/v1/contact/whatsapp/",
        {"phone": "+90 546 450 55 30"},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["phone"] == "+905464505530"
    assert contact_json_path().read_text(encoding="utf-8")
    assert '"+905464505530"' in contact_json_path().read_text(encoding="utf-8")


@pytest.mark.django_db
def test_unwritable_contact_file_returns_json_error(
    api_client, manager, contact_file, monkeypatch
):
    api_client.force_authenticate(manager)

    def boom(_raw):
        raise OSError("Permission denied")

    monkeypatch.setattr("apps.common.contact_views.save_contact_phone", boom)

    response = api_client.patch(
        "/api/v1/contact/whatsapp/",
        {"phone": "+4917643450100"},
        format="json",
    )

    assert response.status_code == 500
    assert "could not be saved" in response.data["detail"]


@pytest.mark.django_db
def test_seller_cannot_update_whatsapp_contact(api_client, seller, contact_file):
    api_client.force_authenticate(seller)

    response = api_client.patch(
        "/api/v1/contact/whatsapp/",
        {"phone": "+4917643450100"},
        format="json",
    )

    assert response.status_code == 403
    assert not contact_file.exists()
