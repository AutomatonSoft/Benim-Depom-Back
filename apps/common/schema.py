from drf_spectacular.openapi import AutoSchema


class MarketplaceAutoSchema(AutoSchema):
    """Keep the OpenAPI document grouped by business domain, not URL version."""

    OPERATION_SUMMARIES = {
        ("GET", "/api/v1/health/"): "Проверить состояние API",
        # Authentication
        ("POST", "/api/v1/auth/register/"): "Зарегистрировать продавца",
        ("POST", "/api/v1/auth/email/verify/"): "Подтвердить email кодом",
        (
            "POST",
            "/api/v1/auth/email/resend-verification/",
        ): "Отправить email-код повторно",
        ("POST", "/api/v1/auth/login/"): "Войти по email и паролю",
        ("POST", "/api/v1/auth/refresh/"): "Обновить JWT-токены",
        ("POST", "/api/v1/auth/logout/"): "Выйти и отозвать refresh-токен",
        ("GET", "/api/v1/auth/me/"): "Получить свой профиль",
        ("PATCH", "/api/v1/auth/me/"): "Изменить свой профиль",
        ("PUT", "/api/v1/auth/me/"): "Заменить свой профиль",
        ("DELETE", "/api/v1/auth/me/"): "Удалить свой аккаунт",
        ("POST", "/api/v1/manager/users/"): "Создать аккаунт менеджера",
        ("GET", "/api/v1/manager/users/sellers/"): "Получить список продавцов",
        ("DELETE", "/api/v1/manager/users/sellers/{user_id}/"): "Удалить продавца",
        (
            "GET",
            "/api/v1/manager/users/sellers/registration-requests/",
        ): "Получить заявки на регистрацию продавцов",
        (
            "POST",
            "/api/v1/manager/users/sellers/{user_id}/approve/",
        ): "Одобрить регистрацию продавца",
        (
            "POST",
            "/api/v1/manager/users/sellers/{user_id}/reject/",
        ): "Отклонить регистрацию продавца",
        
        ("POST", "/api/v1/auth/password/change/"): "Изменить пароль",
        (
            "POST",
            "/api/v1/auth/password/reset/request/",
        ): "Запросить код для сброса пароля",
        (
            "POST",
            "/api/v1/auth/password/reset/verify/",
        ): "Проверить код сброса пароля",
        (
            "POST",
            "/api/v1/auth/password/reset/complete/",
        ): "Установить новый пароль",
        # Product lifecycle
        ("GET", "/api/v1/products/"): "Получить список своих товаров",
        ("POST", "/api/v1/products/"): "Создать товар",
        ("GET", "/api/v1/products/{id}/"): "Получить товар",
        ("PATCH", "/api/v1/products/{id}/"): "Изменить черновик товара",
        ("PUT", "/api/v1/products/{id}/"): "Заменить данные товара",
        ("DELETE", "/api/v1/products/{id}/"): "Архивировать товар",
        ("POST", "/api/v1/products/{product_pk}/images/"): "Загрузить фото товара",
        (
            "POST",
            "/api/v1/products/{product_pk}/images/reorder/",
        ): "Изменить порядок фотографий",
        (
            "DELETE",
            "/api/v1/products/{product_pk}/images/{image_pk}/",
        ): "Удалить фотографию",
        (
            "POST",
            "/api/v1/products/{product_pk}/images/{image_pk}/make-primary/",
        ): "Сделать фото главным",
        (
            "POST",
            "/api/v1/products/{product_pk}/images/{image_pk}/process/",
        ): "Запустить AI-обработку фото",
        (
            "POST",
            "/api/v1/products/{product_pk}/availability/",
        ): "Подтвердить наличие товара",
        (
            "POST",
            "/api/v1/products/{product_pk}/withdraw/",
        ): "Отозвать товар с модерации",
        (
            "POST",
            "/api/v1/products/{product_pk}/deactivate/",
        ): "Запросить отключение товара",
        # Moderation
        (
            "GET",
            "/api/v1/products/{product_pk}/moderation-history/",
        ): "Получить историю модерации",
        ("GET", "/api/v1/manager/products/"): "Получить товары для менеджера",
        (
            "POST",
            "/api/v1/manager/products/{product_pk}/approve/",
        ): "Одобрить товар и назначить EAN",
        ("POST", "/api/v1/manager/products/{product_pk}/reject/"): "Отклонить товар",
        (
            "POST",
            "/api/v1/manager/products/{product_pk}/notifications/",
        ): "Отправить сообщение продавцу",
        (
            "POST",
            "/api/v1/manager/products/{product_pk}/availability-request/",
        ): "Запросить подтверждение наличия",
        (
            "POST",
            "/api/v1/manager/products/{product_pk}/deactivate/",
        ): "Отключить все активные публикации",
        # EAN and notifications
        ("GET", "/api/v1/manager/eans/"): "Получить EAN-коды",
        ("POST", "/api/v1/manager/eans/import/"): "Импортировать EAN-коды",
        ("GET", "/api/v1/manager/eans/summary/"): "Получить остаток EAN-кодов",
        ("POST", "/api/v1/notifications/devices/"): "Зарегистрировать FCM-устройство",
        (
            "POST",
            "/api/v1/notifications/devices/deactivate/",
        ): "Отключить FCM-устройство",
        ("GET", "/api/v1/notifications/"): "Получить свои уведомления",
        (
            "POST",
            "/api/v1/notifications/{notification_pk}/read/",
        ): "Пометить уведомление прочитанным",
        (
            "POST",
            "/api/v1/notifications/read-all/",
        ): "Пометить все уведомления прочитанными",
        # Local OTTO catalog
        (
            "GET",
            "/api/v1/catalog/otto/category-groups/",
        ): "Получить группы категорий OTTO",
        (
            "GET",
            "/api/v1/catalog/otto/category-groups/{group_id}/categories/",
        ): "Получить категории группы OTTO",
        (
            "GET",
            "/api/v1/catalog/otto/category-groups/{group_id}/attributes/",
        ): "Получить атрибуты группы OTTO",
        (
            "GET",
            "/api/v1/catalog/otto/category-groups/{language}/",
        ): "Получить локализованные группы категорий OTTO",
        (
            "GET",
            "/api/v1/catalog/otto/category-groups/{group_id}/categories/{language}/",
        ): "Получить локализованные категории группы OTTO",
        (
            "GET",
            "/api/v1/catalog/otto/category-groups/{group_id}/attributes/{language}/",
        ): "Получить локализованные атрибуты группы OTTO",
        (
            "GET",
            "/api/v1/catalog/otto/shipping-profiles/",
        ): "Получить профили доставки OTTO",
        # Marketplace jobs and publication states
        (
            "POST",
            "/api/v1/orchestrator/products/{product_pk}/{operation}/",
        ): "Запустить операцию маркетплейса",
        (
            "POST",
            "/api/v1/orchestrator/products/{product_pk}/listing-state/",
        ): "Изменить состояние публикаций",
        ("GET", "/api/v1/orchestrator/jobs/{job_id}/"): "Получить статус операции",
        (
            "GET",
            "/api/v1/orchestrator/products/{product_pk}/publications/",
        ): "Получить публикации товара",
        ("GET", "/api/v1/orchestrator/publications/"): "Получить все публикации",
        # OTTO configuration
        (
            "GET",
            "/api/v1/orchestrator/products/{product_pk}/otto/{account}/configuration/",
        ): "Получить конфигурацию OTTO",
        (
            "PATCH",
            "/api/v1/orchestrator/products/{product_pk}/otto/{account}/configuration/",
        ): "Изменить конфигурацию OTTO",
        (
            "GET",
            "/api/v1/orchestrator/products/{product_pk}/otto/{account}/payload-preview/",
        ): "Предпросмотр payload для OTTO",
        # Hood configuration
        (
            "GET",
            "/api/v1/orchestrator/products/{product_pk}/hood/{account}/configuration/",
        ): "Получить конфигурацию Hood",
        (
            "PATCH",
            "/api/v1/orchestrator/products/{product_pk}/hood/{account}/configuration/",
        ): "Изменить конфигурацию Hood",
        (
            "GET",
            "/api/v1/orchestrator/products/{product_pk}/hood/{account}/payload-preview/",
        ): "Предпросмотр payload для Hood",
        # Kaufland configuration
        (
            "GET",
            "/api/v1/orchestrator/products/{product_pk}/kaufland/{account}/configuration/",
        ): "Получить конфигурацию Kaufland",
        (
            "PATCH",
            "/api/v1/orchestrator/products/{product_pk}/kaufland/{account}/configuration/",
        ): "Изменить конфигурацию Kaufland",
        (
            "GET",
            "/api/v1/orchestrator/products/{product_pk}/kaufland/{account}/create-payload-preview/",
        ): "Предпросмотр создания в Kaufland",
        (
            "GET",
            "/api/v1/orchestrator/products/{product_pk}/kaufland/{account}/update-payload-preview/",
        ): "Предпросмотр обновления Kaufland",
        # AI content
        (
            "POST",
            "/api/v1/orchestrator/products/{product_pk}/ai-content/generate/",
        ): "Запустить генерацию AI-контента",
        (
            "GET",
            "/api/v1/orchestrator/ai-content/generations/{generation_id}/",
        ): "Получить статус AI-генерации",
        (
            "POST",
            "/api/v1/orchestrator/products/{product_pk}/ai-content/generations/{generation_id}/apply/",
        ): "Применить AI-контент к конфигурациям",
    }

    def get_summary(self) -> str:
        return self.OPERATION_SUMMARIES.get(
            (self.method.upper(), self.path),
            super().get_summary(),
        )

    def get_tags(self) -> list[str]:
        path = self.path

        if path.startswith("/api/v1/auth/"):
            if any(fragment in path for fragment in ("register/", "email/", "me/")):
                return ["Auth - Seller"]
            return ["Auth - Shared"]
        if path.startswith("/api/v1/manager/users/"):
            return ["Manager accounts"]
        if path.startswith("/api/v1/catalog/otto/shipping-profiles/"):
            return ["OTTO - Delivery"]
        if path.startswith("/api/v1/catalog/otto/"):
            return ["OTTO - Catalog"]
        if path.startswith("/api/v1/manager/eans/"):
            return ["EAN pool"]
        if path.startswith("/api/v1/manager/products/"):
            return ["Manager moderation"]
        if path.startswith("/api/v1/notifications/"):
            return ["Notifications"]
        if path.startswith("/api/v1/products/"):
            if "moderation-history" in path:
                return ["Moderation"]
            if "/images/" in path:
                return ["Product images"]
            return ["Products"]
        if path.startswith("/api/v1/orchestrator/products/") and "/ai-content/" in path:
            return ["AI content"]
        if path.startswith("/api/v1/orchestrator/ai-content/"):
            return ["AI content"]
        if "/orchestrator/products/" in path and "/otto/" in path:
            return ["OTTO"]
        if "/orchestrator/products/" in path and "/hood/" in path:
            return ["Hood"]
        if "/orchestrator/products/" in path and "/kaufland/" in path:
            return ["Kaufland"]
        if path.startswith("/api/v1/orchestrator/"):
            return ["Marketplace jobs"]

        return super().get_tags()
