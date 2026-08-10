import pytest
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APIClient


User = get_user_model()

REGISTER_URL = "/api/v1/auth/register/"
LOGIN_URL = "/api/v1/auth/login/"
REFRESH_URL = "/api/v1/auth/refresh/"
LOGOUT_URL = "/api/v1/auth/logout/"
ME_URL = "/api/v1/auth/me/"


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def password():
    return "N7$kP4mQ2#zL"


@pytest.mark.django_db
def test_user_can_register(api_client, password):
    payload = {
        "username": "seller_test",
        "email": "seller@example.com",
        "password": password,
        "password_confirm": password,
        "phone": "+77000000000",
        "preferred_language": "ru",
    }

    response = api_client.post(REGISTER_URL, payload, format="json")

    assert response.status_code == status.HTTP_201_CREATED
    assert User.objects.filter(username="seller_test").exists()

    user = User.objects.get(username="seller_test")
    assert user.check_password(password)
    assert user.role == User.Role.SELLER
    assert "password" not in response.data


@pytest.mark.django_db
def test_user_can_login_and_read_profile(api_client, password):
    user = User.objects.create_user(
        username="seller_test",
        password=password,
        email="seller@example.com",
    )

    login_response = api_client.post(
        LOGIN_URL,
        {
            "username": user.username,
            "password": password,
        },
        format="json",
    )

    assert login_response.status_code == status.HTTP_200_OK
    assert "access" in login_response.data
    assert "refresh" in login_response.data

    access_token = login_response.data["access"]

    profile_response = api_client.get(
        ME_URL,
        HTTP_AUTHORIZATION=f"Bearer {access_token}",
    )

    assert profile_response.status_code == status.HTTP_200_OK
    assert profile_response.data["username"] == user.username
    assert profile_response.data["role"] == User.Role.SELLER


@pytest.mark.django_db
def test_authenticated_user_can_update_profile(api_client, password):
    user = User.objects.create_user(
        username="seller_test",
        password=password,
    )

    login_response = api_client.post(
        LOGIN_URL,
        {
            "username": user.username,
            "password": password,
        },
        format="json",
    )

    access_token = login_response.data["access"]

    response = api_client.patch(
        ME_URL,
        {
            "phone": "+905550000000",
            "preferred_language": "tr",
        },
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {access_token}",
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data["phone"] == "+905550000000"
    assert response.data["preferred_language"] == "tr"


@pytest.mark.django_db
def test_user_can_logout(api_client, password):
    user = User.objects.create_user(
        username="seller_test",
        password=password,
    )

    login_response = api_client.post(
        LOGIN_URL,
        {
            "username": user.username,
            "password": password,
        },
        format="json",
    )

    access_token = login_response.data["access"]
    refresh_token = login_response.data["refresh"]

    logout_response = api_client.post(
        LOGOUT_URL,
        {"refresh": refresh_token},
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {access_token}",
    )

    assert logout_response.status_code == status.HTTP_204_NO_CONTENT

    refresh_response = api_client.post(
        REFRESH_URL,
        {"refresh": refresh_token},
        format="json",
    )

    assert refresh_response.status_code == status.HTTP_401_UNAUTHORIZED