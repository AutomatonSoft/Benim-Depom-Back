"""Read-only staging load scenarios for the Benim Depom API."""

import os

from locust import HttpUser, between, task


class AuthenticatedApiUser(HttpUser):
    """Base user which logs in once and performs no marketplace mutations."""

    abstract = True
    wait_time = between(1, 3)
    username_env_name = "LOAD_TEST_USERNAME"
    password_env_name = "LOAD_TEST_PASSWORD"

    def on_start(self):
        username = os.environ[self.username_env_name]
        password = os.environ[self.password_env_name]
        response = self.client.post(
            "/api/v1/auth/login/",
            json={"username": username, "password": password},
            name="POST /auth/login/",
        )
        response.raise_for_status()
        self.client.headers.update(
            {"Authorization": f"Bearer {response.json()['access']}"}
        )


class SellerReadUser(AuthenticatedApiUser):
    weight = 4

    @task(5)
    def list_products(self):
        self.client.get("/api/v1/products/?page=1", name="GET /products/")

    @task(2)
    def list_notifications(self):
        self.client.get("/api/v1/notifications/?page=1", name="GET /notifications/")

    @task(1)
    def list_otto_groups(self):
        self.client.get(
            "/api/v1/catalog/otto/category-groups/?page=1&limit=50",
            name="GET /catalog/otto/category-groups/",
        )


class ManagerReadUser(AuthenticatedApiUser):
    weight = 1
    username_env_name = "LOAD_TEST_MANAGER_USERNAME"
    password_env_name = "LOAD_TEST_MANAGER_PASSWORD"

    @task(4)
    def list_manager_products(self):
        self.client.get(
            "/api/v1/manager/products/?page=1",
            name="GET /manager/products/",
        )

    @task(1)
    def list_ean_summary(self):
        self.client.get(
            "/api/v1/manager/eans/summary/",
            name="GET /manager/eans/summary/",
        )
