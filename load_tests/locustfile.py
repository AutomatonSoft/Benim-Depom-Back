"""Safe staging load scenarios for the Benim Depom API.

The default profile is read-only: it never publishes, changes listings,
generates AI content, uploads files, sends e-mail, or imports EANs.
"""

import os
import time

from locust import HttpUser, between, task
from locust.exception import StopUser


ACCESS_REFRESH_AFTER_SECONDS = 10 * 60


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


class ApiUser(HttpUser):
    """Authenticated user. Prefers login+refresh so long staged runs survive
    the 15-minute access-token TTL. A shared ACCESS_TOKEN env value cannot
    be refreshed safely (refresh rotation blacklists the one token).
    """

    abstract = True
    wait_time = between(1, 3)
    token_env_name = ""
    username_env_name = ""
    email_env_name = ""
    password_env_name = ""

    def on_start(self):
        self.refresh_token = ""
        self.access_obtained_at = 0.0
        self.email = env(self.email_env_name) or env(self.username_env_name)
        self.password = env(self.password_env_name)
        access = env(self.token_env_name)

        if self.email and self.password:
            if not self._login():
                raise StopUser(f"Login failed for {self.email}.")
            return

        if access:
            self._set_access(access, refresh="")
            return

        raise StopUser(
            f"Set {self.username_env_name}/{self.password_env_name} "
            f"(preferred for long runs) or {self.token_env_name}."
        )

    def _set_access(self, access: str, refresh: str) -> None:
        self.client.headers.update({"Authorization": f"Bearer {access}"})
        self.refresh_token = refresh
        self.access_obtained_at = time.monotonic()

    def _login(self) -> bool:
        with self.client.post(
            "/api/v1/auth/login/",
            json={"email": self.email, "password": self.password},
            name="POST /auth/login/ (setup)",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"HTTP {response.status_code}")
                return False
            payload = response.json()
            self._set_access(payload["access"], payload.get("refresh", ""))
            response.success()
            return True

    def _refresh(self) -> bool:
        if not self.refresh_token:
            return False
        with self.client.post(
            "/api/v1/auth/refresh/",
            json={"refresh": self.refresh_token},
            name="POST /auth/refresh/ (setup)",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.success()
                return False
            payload = response.json()
            self._set_access(
                payload["access"],
                payload.get("refresh", self.refresh_token),
            )
            response.success()
            return True

    def _ensure_fresh_access(self) -> None:
        if not self.refresh_token:
            return
        age = time.monotonic() - self.access_obtained_at
        if age < ACCESS_REFRESH_AFTER_SECONDS:
            return
        if not self._refresh() and self.email and self.password:
            self._login()

    def _recover_auth(self) -> bool:
        if self._refresh():
            return True
        if self.email and self.password:
            return self._login()
        return False

    def get_ok(self, url: str, name: str, expected: tuple[int, ...] = (200,)):
        self._ensure_fresh_access()
        with self.client.get(url, name=name, catch_response=True) as response:
            if response.status_code in expected:
                response.success()
                return
            if response.status_code == 401 and self._recover_auth():
                response.success()
            else:
                response.failure(f"HTTP {response.status_code}")
                return

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


class SellerReadUser(ApiUser):
    """Read actions an authenticated seller performs most often."""

    weight = 5
    token_env_name = "LOAD_TEST_SELLER_ACCESS_TOKEN"
    username_env_name = "LOAD_TEST_SELLER_USERNAME"
    email_env_name = "LOAD_TEST_SELLER_EMAIL"
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
    email_env_name = "LOAD_TEST_MANAGER_EMAIL"
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

    @task(1)
    def shipping_profiles(self):
        self.get_ok(
            f"/api/v1/catalog/otto/shipping-profiles/?account={self.account}",
            "GET /catalog/otto/shipping-profiles/",
        )

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
