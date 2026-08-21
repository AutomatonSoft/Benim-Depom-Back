import pytest

from apps.notifications.models import DeviceToken, Notification


def authenticate(client, user):
    client.force_authenticate(user=user)
    return client


@pytest.mark.integration
@pytest.mark.django_db
def test_device_token_is_registered_reassigned_and_deactivated(api_client, seller, second_seller):
    authenticate(api_client, seller)
    response = api_client.post(
        "/api/v1/notifications/devices/", {"token": "device-token", "platform": "android"}, format="json"
    )
    assert response.status_code == 201
    assert DeviceToken.objects.get(token="device-token").user == seller

    authenticate(api_client, second_seller)
    response = api_client.post(
        "/api/v1/notifications/devices/", {"token": "device-token", "platform": "ios"}, format="json"
    )
    assert response.status_code == 201
    device = DeviceToken.objects.get(token="device-token")
    assert device.user == second_seller and device.platform == "ios"

    response = api_client.post("/api/v1/notifications/devices/deactivate/", {"token": "device-token"}, format="json")
    assert response.status_code == 204
    device.refresh_from_db()
    assert device.is_active is False


@pytest.mark.integration
@pytest.mark.django_db
def test_device_token_rejects_invalid_platform_and_cannot_be_disabled_by_other_user(
    api_client,
    seller,
    second_seller,
):
    authenticate(api_client, seller)
    assert api_client.post(
        "/api/v1/notifications/devices/",
        {"token": "seller-device-token", "platform": "android"},
        format="json",
    ).status_code == 201

    invalid_platform = api_client.post(
        "/api/v1/notifications/devices/",
        {"token": "invalid-device-token", "platform": "desktop"},
        format="json",
    )
    assert invalid_platform.status_code == 400

    authenticate(api_client, second_seller)
    assert api_client.post(
        "/api/v1/notifications/devices/deactivate/",
        {"token": "seller-device-token"},
        format="json",
    ).status_code == 204
    assert DeviceToken.objects.get(token="seller-device-token").is_active is True


@pytest.mark.integration
@pytest.mark.django_db
def test_notifications_are_private_and_can_be_marked_read(api_client, seller, second_seller):
    notification = Notification.objects.create(
        user=seller,
        notification_type=Notification.Type.MANAGER_MESSAGE,
        title="Question",
        body="Is it available?",
    )
    authenticate(api_client, second_seller)
    assert api_client.post(f"/api/v1/notifications/{notification.id}/read/").status_code == 404

    authenticate(api_client, seller)
    response = api_client.get("/api/v1/notifications/")
    assert response.status_code == 200
    assert response.data["results"][0]["id"] == notification.id
    assert api_client.post(f"/api/v1/notifications/{notification.id}/read/").status_code == 200
    notification.refresh_from_db()
    assert notification.is_read is True and notification.read_at is not None
    assert api_client.post("/api/v1/notifications/read-all/").status_code == 204


@pytest.mark.integration
@pytest.mark.django_db
def test_manager_can_message_product_owner_but_seller_cannot(api_client, seller, manager, product_factory):
    product = product_factory(owner=seller)
    authenticate(api_client, seller)
    assert api_client.post(
        f"/api/v1/manager/products/{product.id}/notifications/", {"body": "Hello"}, format="json"
    ).status_code == 403

    authenticate(api_client, manager)
    assert api_client.post(
        f"/api/v1/manager/products/{product.id}/notifications/", {"body": "   "}, format="json"
    ).status_code == 400
    response = api_client.post(
        f"/api/v1/manager/products/{product.id}/notifications/",
        {"title": "Availability", "body": "Please confirm stock."},
        format="json",
    )
    assert response.status_code == 201
    assert Notification.objects.filter(user=seller, product=product).exists()
