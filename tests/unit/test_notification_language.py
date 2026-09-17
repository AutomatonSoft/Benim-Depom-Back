import pytest

from apps.notifications.copy import render_notification_copy
from apps.notifications.models import Notification
from apps.notifications.services import create_notification


@pytest.mark.unit
def test_notification_copy_uses_requested_language():
    title, body = render_notification_copy(
        key="product_changes_approved",
        language="tr",
        name="Krovat",
    )
    assert title == "Ürün değişiklikleri onaylandı"
    assert "Krovat" in body
    assert "approved" not in body


@pytest.mark.unit
def test_product_sold_copy_has_no_marketplace():
    title, body = render_notification_copy(
        key="product_sold",
        language="ru",
        name="Стул",
        sold_at="16.09.2026 12:00",
        card_price="100.00",
        currency="TRY",
        qty_sold="2",
        qty_before="20",
        qty_after="20",
    )
    assert title == "Ваш товар купили"
    assert "Стул" in body
    assert "OTTO" not in body
    assert "Hood" not in body


@pytest.mark.integration
@pytest.mark.django_db
def test_preferred_language_endpoint_updates_profile(api_client, seller):
    api_client.force_authenticate(user=seller)
    response = api_client.patch(
        "/api/v1/auth/me/language/",
        {"language": "TR"},
        format="json",
    )
    assert response.status_code == 200, response.data
    assert response.data["preferred_language"] == "tr"
    seller.refresh_from_db()
    assert seller.preferred_language == "tr"

    rejected = api_client.patch(
        "/api/v1/auth/me/language/",
        {"language": "fr"},
        format="json",
    )
    assert rejected.status_code == 400


@pytest.mark.integration
@pytest.mark.django_db
def test_seller_notifications_use_preferred_language(seller, product_factory):
    seller.preferred_language = "tr"
    seller.save(update_fields=["preferred_language"])
    product = product_factory(owner=seller, title="Kreslo")
    notification = create_notification(
        user=seller,
        product=product,
        notification_type=Notification.Type.PRODUCT_APPROVED,
    )
    assert notification.title == "Ürün onayı"
    assert "Kreslo" in notification.body
    assert "approved" not in notification.body.lower()
