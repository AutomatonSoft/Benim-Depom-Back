from unittest.mock import patch
from urllib.parse import urlsplit

import pytest
from django.urls import resolve, reverse
from drf_spectacular.generators import SchemaGenerator

from apps.accounts.models import User
from apps.accounts.services import issue_email_verification_code
from apps.products.models import Product

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


def test_legacy_app_can_register_verify_and_login_without_phone(api_client, password):
    payload = {
        "username": "old-app",
        "email": "old-app@example.com",
        "first_name": "Alihan",
        "last_name": "Bulkair",
        "password": password,
        "password_confirm": password,
    }
    registered = api_client.post("/api/v1/auth/register/", payload, format="json")
    assert registered.status_code == 201, registered.data
    assert registered.data["user"]["phone"] == ""
    user = User.objects.get(pk=registered.data["user"]["id"])
    code = issue_email_verification_code(user=user)
    verified = api_client.post(
        "/api/v1/auth/email/verify/", {"email": user.email, "code": code}, format="json"
    )
    assert verified.status_code == 200
    assert verified.data["user"]["phone"] == ""
    credentials = {"email": user.email, "password": password}
    login = api_client.post("/api/v1/auth/login/", credentials, format="json")
    assert login.status_code == 200
    assert (
        api_client.post("/api/v2/auth/login/", credentials, format="json").status_code
        == 401
    )
    # Tokens identify accounts, so switching API versions does not invalidate sessions.
    me = api_client.get(
        "/api/v2/auth/me/", HTTP_AUTHORIZATION=f"Bearer {login.data['access']}"
    )
    assert me.status_code == 200 and me.data["id"] == user.pk


def test_auth_versions_have_distinct_registration_contracts(api_client, password):
    payload = {
        "username": "new-app",
        "email": "shared@example.com",
        "first_name": "Alihan",
        "last_name": "Bulkair",
        "password": password,
        "password_confirm": password,
    }
    assert (
        api_client.post("/api/v2/auth/register/", payload, format="json").status_code
        == 400
    )
    for phone in ["+905551111111", "+905552222222"]:
        response = api_client.post(
            "/api/v2/auth/register/", {**payload, "phone": phone}, format="json"
        )
        assert response.status_code == 201, response.data
    legacy = api_client.post(
        "/api/v1/auth/register/", {**payload, "phone": "+905553333333"}, format="json"
    )
    assert legacy.status_code == 400 and "email" in legacy.data


def test_v1_never_guesses_which_shared_email_account_to_authenticate(
    api_client, user_factory, password, django_capture_on_commit_callbacks
):
    first = user_factory(email="shared@example.com")
    second = user_factory(email=first.email, password="OtherPassword123!")
    for credential in [password, "OtherPassword123!"]:
        login = api_client.post(
            "/api/v1/auth/login/",
            {"email": first.email, "password": credential},
            format="json",
        )
        assert login.status_code == 401
    with patch("apps.accounts.views.send_password_reset_code.delay") as send:
        with django_capture_on_commit_callbacks(execute=True):
            result = api_client.post(
                "/api/v1/auth/password/reset/request/",
                {"email": first.email},
                format="json",
            )
        assert result.status_code == 202
        send.assert_not_called()
    login = api_client.post(
        "/api/v2/auth/login/",
        {"phone": second.phone, "password": "OtherPassword123!"},
        format="json",
    )
    assert login.status_code == 200


def test_v2_requires_phone_for_email_codes_and_password_reset(api_client):
    for endpoint, payload in [
        ("email/verify/", {"email": "test@example.com", "code": "123456"}),
        ("email/resend-verification/", {"email": "test@example.com"}),
        ("password/reset/request/", {"email": "test@example.com"}),
        ("password/reset/verify/", {"email": "test@example.com", "code": "123456"}),
    ]:
        response = api_client.post(f"/api/v2/auth/{endpoint}", payload, format="json")
        assert response.status_code == 400 and "phone" in response.data


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_products_and_permissions_share_the_same_data(
    api_client, seller, manager, product_factory, version
):
    product = product_factory(owner=seller, status=Product.Status.APPROVED)
    api_client.force_authenticate(seller)
    response = api_client.get(f"/api/{version}/products/{product.pk}/")
    assert response.status_code == 200 and response.data["id"] == product.pk
    assert api_client.get(f"/api/{version}/manager/products/").status_code == 403
    api_client.force_authenticate(manager)
    assert api_client.get(f"/api/{version}/manager/products/").status_code == 200


def test_versioned_routes_keep_legacy_reverse_names():
    assert reverse("accounts:login") == "/api/v1/auth/login/"
    assert reverse("v2:accounts:login") == "/api/v2/auth/login/"
    for suffix in [
        "health/",
        "auth/me/",
        "products/",
        "notifications/",
        "catalog/otto/category-groups/en/",
        "manager/products/",
        "orchestrator/publications/",
    ]:
        assert resolve(f"/api/v1/{suffix}")
        assert resolve(f"/api/v2/{suffix}")


@pytest.mark.parametrize("version", ["v1", "v2"])
@pytest.mark.django_db(transaction=True)
def test_created_photo_and_pagination_urls_stay_in_the_requested_version(
    api_client, seller, product_type, image_file, product_factory, version
):
    from tests.integration.test_products_api import multipart_product_payload

    api_client.force_authenticate(seller)
    with patch("apps.products.media_uploads.enqueue_photo_upload"):
        created = api_client.post(
            f"/api/{version}/products/",
            multipart_product_payload(product_type, image_file),
            format="multipart",
        )
        assert created.status_code == 201, created.data
    for field in ("image", "thumbnail"):
        url = urlsplit(created.data["images"][0][field]).path
        assert url.startswith(f"/api/{version}/products/staged-photo/")
        shown = api_client.get(url)
        assert shown.status_code == 200
        shown.close()
    for _ in range(10):
        product_factory(owner=seller, status=Product.Status.SUBMITTED)
    page = api_client.get(f"/api/{version}/products/")
    assert page.status_code == 200
    assert urlsplit(page.data["next"]).path == f"/api/{version}/products/"


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_cookie_login_uses_the_same_version_path(api_client, seller, password, version):
    credentials = (
        {"email": seller.email} if version == "v1" else {"phone": seller.phone}
    )
    response = api_client.post(
        f"/api/{version}/auth/login/",
        {**credentials, "password": password},
        format="json",
        HTTP_X_REFRESH_TOKEN_COOKIE="seller",
    )
    assert response.status_code == 200
    assert response.cookies["benim_refresh"]["path"] == f"/api/{version}/auth/"


def test_each_schema_describes_only_its_own_auth_contract():
    from config.urls import v1_routes, v2_routes

    for version, patterns, expected, other in [
        ("v1", v1_routes, "email", "phone"),
        ("v2", v2_routes, "phone", None),
    ]:
        schema = SchemaGenerator(patterns=patterns).get_schema(
            request=None, public=True
        )
        assert all(path.startswith(f"/api/{version}/") for path in schema["paths"])
        register = schema["paths"][f"/api/{version}/auth/register/"]["post"]
        ref = register["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        required = schema["components"]["schemas"][ref.rsplit("/", 1)[-1]]["required"]
        assert expected in required
        if other:
            assert other not in required
        login = schema["paths"][f"/api/{version}/auth/login/"]["post"]
        ref = login["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        properties = schema["components"]["schemas"][ref.rsplit("/", 1)[-1]][
            "properties"
        ]
        assert expected in properties
        if other:
            assert other not in properties


def test_docs_are_separate_and_unknown_versions_are_not_aliased(api_client):
    for path in ["/api/v1/docs/", "/api/v2/docs/"]:
        assert api_client.get(path).status_code == 200
    assert api_client.get("/api/v3/health/").status_code == 404
