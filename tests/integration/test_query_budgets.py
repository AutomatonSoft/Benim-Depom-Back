import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext


@pytest.mark.integration
@pytest.mark.django_db
def test_seller_product_list_query_budget(api_client, seller, product_factory):
    for index in range(25):
        product_factory(owner=seller, title=f"Seller product {index}")

    api_client.force_authenticate(seller)
    with CaptureQueriesContext(connection) as queries:
        response = api_client.get("/api/v1/products/")

    assert response.status_code == 200
    assert response.data["count"] == 25
    # One extra query loads the current EUR rate for listing_price_eur.
    assert len(queries) <= 7


@pytest.mark.integration
@pytest.mark.django_db
def test_manager_product_list_query_budget(
    api_client,
    manager,
    seller,
    second_seller,
    product_factory,
):
    for index in range(15):
        product_factory(owner=seller, title=f"First seller {index}")
        product_factory(owner=second_seller, title=f"Second seller {index}")

    api_client.force_authenticate(manager)
    with CaptureQueriesContext(connection) as queries:
        response = api_client.get("/api/v1/manager/products/")

    assert response.status_code == 200
    assert response.data["count"] == 30
    # One extra query loads the current EUR rate for listing_price_eur.
    assert len(queries) <= 7
