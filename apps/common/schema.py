from drf_spectacular.openapi import AutoSchema


class MarketplaceAutoSchema(AutoSchema):
    """Group Swagger operations by client and business domain."""

    OPERATION_SUMMARIES = {
        ("GET", "/api/v1/health/"): "Проверить состояние API",
        ("POST", "/api/v1/auth/register/"): "Зарегистрировать продавца",
        ("POST", "/api/v1/auth/login/"): "Войти по логину и паролю",
        ("POST", "/api/v1/auth/refresh/"): "Обновить JWT-токены",
        ("POST", "/api/v1/auth/logout/"): "Выйти и отозвать refresh-токен",
        ("GET", "/api/v1/auth/me/"): "Получить свой профиль",
        ("PATCH", "/api/v1/auth/me/"): "Изменить свой профиль",
        ("PUT", "/api/v1/auth/me/"): "Заменить свой профиль",
        ("POST", "/api/v1/auth/phone/verify/"): "Подтвердить телефон через Firebase",
        ("POST", "/api/v1/manager/users/"): "Создать менеджера",
        ("GET", "/api/v1/catalog/categories/"): "Получить категории",
        ("POST", "/api/v1/catalog/categories/"): "Создать категорию",
        ("GET", "/api/v1/catalog/categories/{id}/"): "Получить категорию",
        ("PATCH", "/api/v1/catalog/categories/{id}/"): "Изменить категорию",
        ("PUT", "/api/v1/catalog/categories/{id}/"): "Заменить категорию",
        ("DELETE", "/api/v1/catalog/categories/{id}/"): "Удалить категорию",
        ("GET", "/api/v1/products/"): "Получить список товаров",
        ("POST", "/api/v1/products/"): "Создать товар",
        ("GET", "/api/v1/products/{id}/"): "Получить товар",
        ("PATCH", "/api/v1/products/{id}/"): "Изменить товар",
        ("PUT", "/api/v1/products/{id}/"): "Заменить товар",
        ("DELETE", "/api/v1/products/{id}/"): "Архивировать товар",
        ("POST", "/api/v1/products/{product_pk}/images/"): "Загрузить фотографию товара",
        ("POST", "/api/v1/products/{product_pk}/images/reorder/"): "Изменить порядок фотографий",
        ("DELETE", "/api/v1/products/{product_pk}/images/{image_pk}/"): "Удалить фотографию товара",
        ("POST", "/api/v1/products/{product_pk}/images/{image_pk}/make-primary/"): "Сделать фотографию главной",
        ("POST", "/api/v1/products/{product_pk}/images/{image_pk}/process/"): "Запустить AI-обработку фотографии",
        ("POST", "/api/v1/products/{product_pk}/availability/"): "Подтвердить наличие товара",
        ("POST", "/api/v1/products/{product_pk}/withdraw/"): "Отозвать товар с модерации",
        ("POST", "/api/v1/products/{product_pk}/deactivate/"): "Запросить деактивацию товара",
        ("POST", "/api/v1/products/{product_pk}/submit/"): "Отправить товар на модерацию",
        ("GET", "/api/v1/products/{product_pk}/moderation-history/"): "Получить историю модерации",
        ("GET", "/api/v1/manager/products/"): "Получить товары для модерации",
        ("POST", "/api/v1/manager/products/{product_pk}/approve/"): "Одобрить товар",
        ("POST", "/api/v1/manager/products/{product_pk}/reject/"): "Отклонить товар",
        ("POST", "/api/v1/manager/products/{product_pk}/notifications/"): "Отправить уведомление продавцу",
        ("POST", "/api/v1/manager/products/{product_pk}/availability-request/"): "Запросить наличие товара",
        ("POST", "/api/v1/manager/products/{product_pk}/deactivate/"): "Подтвердить деактивацию товара",
        ("GET", "/api/v1/manager/eans/"): "Получить EAN-коды",
        ("POST", "/api/v1/manager/eans/import/"): "Импортировать EAN-коды",
        ("GET", "/api/v1/manager/eans/summary/"): "Получить остаток EAN-кодов",
        ("POST", "/api/v1/notifications/devices/"): "Зарегистрировать FCM-устройство",
        ("POST", "/api/v1/notifications/devices/deactivate/"): "Отключить FCM-устройство",
        ("GET", "/api/v1/notifications/"): "Получить свои уведомления",
        ("POST", "/api/v1/notifications/{notification_pk}/read/"): "Прочитать уведомление",
        ("POST", "/api/v1/notifications/read-all/"): "Прочитать все уведомления",
    }

    def get_summary(self) -> str:
        return self.OPERATION_SUMMARIES.get(
            (self.method.upper(), self.path),
            super().get_summary(),
        )

    def get_tags(self) -> list[str]:
        path = self.path

        if path.startswith("/api/v1/health/"):
            return ["Service"]
        if path.startswith("/api/v1/auth/"):
            if any(
                fragment in path
                for fragment in ("register/", "me/", "phone/verify/")
            ):
                return ["Mobile — Authentication"]
            return ["Authentication — Shared"]
        if path.startswith("/api/v1/manager/users/"):
            return ["Web — Manager accounts"]
        if path.startswith("/api/v1/catalog/"):
            return ["Catalog"]
        if path.startswith("/api/v1/manager/eans/"):
            return ["Web — EAN pool"]
        if path.startswith("/api/v1/manager/products/"):
            return ["Web — Moderation"]
        if path.startswith("/api/v1/notifications/"):
            return ["Notifications"]
        if path.startswith("/api/v1/products/"):
            if "moderation-history" in path or path.endswith("submit/"):
                return ["Moderation"]
            return ["Products"]

        return super().get_tags()
