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
def test_product_sold_copy_asks_manager_to_update_stock():
    title, body = render_notification_copy(
        key="product_sold",
        language="ru",
        name="Стул",
        sold_at="16.09.2026 12:00",
        qty_sold="2",
        marketplace="OTTO",
    )
    assert title == "Товар купили в Afterbuy"
    assert "Стул" in body
    assert "Afterbuy" in body
    assert "OTTO" in body
    assert "уведомите продавца" in body
    assert "складе" in body


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

    english = api_client.patch(
        "/api/v1/auth/me/language/",
        {"language": "en-US"},
        format="json",
    )
    assert english.status_code == 200, english.data
    assert english.data["preferred_language"] == "en"
    seller.refresh_from_db()
    assert seller.preferred_language == "en"


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


@pytest.mark.integration
@pytest.mark.django_db
def test_english_notifications_use_en_copy_not_turkish(seller, product_factory):
    seller.preferred_language = "en"
    seller.save(update_fields=["preferred_language"])
    product = product_factory(owner=seller, title="White sofa")
    approved = create_notification(
        user=seller,
        product=product,
        notification_type=Notification.Type.PRODUCT_APPROVED,
    )
    rejected = create_notification(
        user=seller,
        product=product,
        notification_type=Notification.Type.PRODUCT_REJECTED,
    )
    reminder_title, reminder_body = render_notification_copy(
        key="product_availability_reminder",
        language="en-US",
        name="White sofa",
    )
    assert approved.title == "Product approval"
    assert "approved" in approved.body.lower()
    assert rejected.title == "Product rejected"
    assert "Ürün" not in rejected.title
    assert reminder_title == "Product availability"
    assert "mevcut" not in reminder_body
