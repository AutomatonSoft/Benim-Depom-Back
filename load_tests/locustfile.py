"""Safe staging load scenarios for the Benim Depom API.

The default profile is read-only: it never publishes, changes listings,
generates AI content, uploads files, sends e-mail, or imports EANs.
"""

import os

from locust import HttpUser, between, task
from locust.exception import StopUser


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


class ApiUser(HttpUser):
    """Authenticated user; use an access token to avoid testing login throttling."""

    abstract = True
    wait_time = between(1, 3)
    token_env_name = ""
    username_env_name = ""
    password_env_name = ""

    def on_start(self):
        token = env(self.token_env_name)
        if not token:
            username = env(self.username_env_name)
            password = env(self.password_env_name)
            if not username or not password:
                raise StopUser(
                    f"Set {self.token_env_name} or both "
                    f"{self.username_env_name}/{self.password_env_name}."
                )

            response = self.client.post(
                "/api/v1/auth/login/",
                json={"username": username, "password": password},
                name="POST /auth/login/ (setup)",
            )
            if response.status_code != 200:
                raise StopUser(f"Login failed: HTTP {response.status_code}")
            token = response.json()["access"]

        self.client.headers.update({"Authorization": f"Bearer {token}"})

    def get_ok(self, url: str, name: str, expected: tuple[int, ...] = (200,)):
        with self.client.get(url, name=name, catch_response=True) as response:
            if response.status_code in expected:
                response.success()
            else:
                response.failure(f"HTTP {response.status_code}")


class PublicCatalogUser(HttpUser):
    """Unauthenticated catalog and health traffic from a mobile app."""

    weight = 2
    wait_time = between(1, 2)
    group_id = env("LOAD_TEST_OTTO_GROUP_ID", "3593")

    @task(6)
    def health(self):
        self.client.get("/api/v1/health/", name="GET /health/")

    @task(4)
    def category_groups(self):
        self.client.get(
            "/api/v1/catalog/otto/category-groups/?page=1&limit=50",
            name="GET /catalog/otto/category-groups/",
        )

    @task(2)
    def turkish_category_groups(self):
        self.client.get(
            "/api/v1/catalog/otto/category-groups/tr/?page=1&limit=50",
            name="GET /catalog/otto/category-groups/tr/",
        )

    @task(3)
    def categories(self):
        self.client.get(
            f"/api/v1/catalog/otto/category-groups/{self.group_id}/categories/"
            "?page=1&limit=50",
            name="GET /catalog/otto/category-groups/:id/categories/",
        )

    @task(2)
    def attributes(self):
        self.client.get(
            f"/api/v1/catalog/otto/category-groups/{self.group_id}/attributes/"
            "?page=1&limit=50",
            name="GET /catalog/otto/category-groups/:id/attributes/",
        )

    @task(1)
    def shipping_profiles(self):
        self.client.get(
            "/api/v1/catalog/otto/shipping-profiles/",
            name="GET /catalog/otto/shipping-profiles/",
        )


class SellerReadUser(ApiUser):
    """Read actions an authenticated seller performs most often."""

    weight = 5
    token_env_name = "LOAD_TEST_SELLER_ACCESS_TOKEN"
    username_env_name = "LOAD_TEST_SELLER_USERNAME"
    password_env_name = "LOAD_TEST_SELLER_PASSWORD"
    product_id = env("LOAD_TEST_SELLER_PRODUCT_ID")

    @task(4)
    def profile(self):
        self.get_ok("/api/v1/auth/me/", "GET /auth/me/")

    @task(8)
    def products(self):
        self.get_ok("/api/v1/products/?page=1&limit=20", "GET /products/")

    @task(2)
    def filtered_products(self):
        self.get_ok(
            "/api/v1/products/?page=1&limit=20&ordering=-updated_at",
            "GET /products/ (filtered)",
        )

    @task(3)
    def notifications(self):
        self.get_ok("/api/v1/notifications/?page=1&limit=20", "GET /notifications/")

    @task(2)
    def product_detail(self):
        if self.product_id:
            self.get_ok(
                f"/api/v1/products/{self.product_id}/",
                "GET /products/:id/",
            )

    @task(1)
    def moderation_history(self):
        if self.product_id:
            self.get_ok(
                f"/api/v1/products/{self.product_id}/moderation-history/?page=1",
                "GET /products/:id/moderation-history/",
            )


class ManagerReadUser(ApiUser):
    """Read actions of the manager web panel."""

    weight = 2
    token_env_name = "LOAD_TEST_MANAGER_ACCESS_TOKEN"
    username_env_name = "LOAD_TEST_MANAGER_USERNAME"
    password_env_name = "LOAD_TEST_MANAGER_PASSWORD"
    product_id = env("LOAD_TEST_MANAGER_PRODUCT_ID")
    account = env("LOAD_TEST_MARKETPLACE_ACCOUNT", "jv")

    @task(3)
    def profile(self):
        self.get_ok("/api/v1/auth/me/", "GET /auth/me/ (manager)")

    @task(8)
    def manager_products(self):
        self.get_ok(
            "/api/v1/manager/products/?page=1&limit=20&ordering=-updated_at",
            "GET /manager/products/",
        )

    @task(4)
    def sellers(self):
        self.get_ok(
            "/api/v1/manager/users/sellers/?page=1&limit=20",
            "GET /manager/users/sellers/",
        )

    @task(3)
    def ean_summary(self):
        self.get_ok("/api/v1/manager/eans/summary/", "GET /manager/eans/summary/")

    @task(3)
    def eans(self):
        self.get_ok("/api/v1/manager/eans/?page=1&limit=20", "GET /manager/eans/")

    @task(4)
    def publications(self):
        self.get_ok(
            "/api/v1/orchestrator/publications/?page=1&limit=20",
            "GET /orchestrator/publications/",
        )

    @task(2)
    def product_publications(self):
        if self.product_id:
            self.get_ok(
                f"/api/v1/orchestrator/products/{self.product_id}/publications/",
                "GET /orchestrator/products/:id/publications/",
            )

    @task(1)
    def otto_preview(self):
        if self.product_id:
            self.get_ok(
                f"/api/v1/orchestrator/products/{self.product_id}/otto/"
                f"{self.account}/payload-preview/",
                "GET /orchestrator/products/:id/otto/:account/payload-preview/",
                expected=(200, 400),
            )

    @task(1)
    def hood_preview(self):
        if self.product_id:
            self.get_ok(
                f"/api/v1/orchestrator/products/{self.product_id}/hood/"
                f"{self.account}/payload-preview/",
                "GET /orchestrator/products/:id/hood/:account/payload-preview/",
                expected=(200, 400),
            )

    @task(1)
    def kaufland_create_preview(self):
        if self.product_id:
            self.get_ok(
                f"/api/v1/orchestrator/products/{self.product_id}/kaufland/"
                f"{self.account}/create-payload-preview/",
                "GET /orchestrator/products/:id/kaufland/:account/create-preview/",
                expected=(200, 400),
            )
