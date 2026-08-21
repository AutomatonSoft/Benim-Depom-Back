from datetime import timedelta
from pathlib import Path

import environ
from celery.schedules import crontab
from kombu import Exchange, Queue

BASE_DIR = Path(__file__).resolve().parents[2]

env = environ.Env()

SECRET_KEY = env("DJANGO_SECRET_KEY")

ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "corsheaders",
    "rest_framework",
    "drf_spectacular",
    "rest_framework_simplejwt.token_blacklist",
]

LOCAL_APPS = [
    "apps.accounts.apps.AccountsConfig",
    "apps.catalog.apps.CatalogConfig",
    "apps.products.apps.ProductsConfig",
    "apps.moderation.apps.ModerationConfig",
    "apps.notifications.apps.NotificationsConfig",
    "apps.ean.apps.EanConfig",
    "apps.orchestrator.apps.OrchestratorConfig",
    "apps.marketplace.hood.apps.HoodConfig",
    "apps.marketplace.otto.apps.OttoConfig",
    "apps.marketplace.kaufland.apps.KauflandConfig",
    "apps.idempotency.apps.IdempotencyConfig",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB"),
        "USER": env("POSTGRES_USER"),
        "PASSWORD": env("POSTGRES_PASSWORD"),
        "HOST": env("POSTGRES_HOST"),
        "PORT": env("POSTGRES_PORT"),
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

FTP_MEDIA_HOST = env("FTP_MEDIA_HOST")
FTP_MEDIA_PORT = env.int("FTP_MEDIA_PORT", default=21)
FTP_MEDIA_USERNAME = env("FTP_MEDIA_USERNAME")
FTP_MEDIA_PASSWORD = env("FTP_MEDIA_PASSWORD")
FTP_MEDIA_REMOTE_ROOT = env("FTP_MEDIA_REMOTE_ROOT")
FTP_MEDIA_PUBLIC_BASE_URL = env("FTP_MEDIA_PUBLIC_BASE_URL")
FTP_MEDIA_USE_TLS = env.bool("FTP_MEDIA_USE_TLS", default=True)
FTP_MEDIA_PASSIVE_MODE = env.bool(
    "FTP_MEDIA_PASSIVE_MODE",
    default=True,
)
FTP_MEDIA_TIMEOUT_SECONDS = env.int(
    "FTP_MEDIA_TIMEOUT_SECONDS",
    default=30,
)

USE_FTP_MEDIA_STORAGE = True

STORAGES = {
    "default": {
        "BACKEND": "apps.common.ftp_storage.FTPMediaStorage",
    },
    "staticfiles": {
        "BACKEND": ("django.contrib.staticfiles.storage.StaticFilesStorage"),
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "accounts.User"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_SCHEMA_CLASS": "apps.common.schema.MarketplaceAutoSchema",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_THROTTLE_CLASSES": [
        "apps.common.throttles.ManagerMutationRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "registration": "5/hour",
        "login": "10/15m",
        "ai_generation": "10/hour",
        "image_upload": "60/hour",
        "manager_mutation": "120/hour",
        "email_verification": "10/hour",
        "email_verification_resend": "3/hour",
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Marketplace Backend API",
    "DESCRIPTION": (
        "API for the seller mobile application and manager web panel. "
        "Operations are grouped by client and business domain."
    ),
    "VERSION": "1.0.0",
    "COMPONENT_SPLIT_REQUEST": True,
    "TAGS": [
        {
            "name": "Mobile — Authentication",
            "description": "Seller registration, profile and Firebase phone verification.",
        },
        {
            "name": "Authentication — Shared",
            "description": "JWT login, refresh and logout for both clients.",
        },
        {
            "name": "Catalog",
            "description": "Product categories and product types.",
        },
        {
            "name": "Products",
            "description": "Seller products, variants and source images.",
        },
        {
            "name": "Moderation",
            "description": "Seller submission and moderation history.",
        },
        {
            "name": "Web — Moderation",
            "description": "Manager approval, rejection and seller communication.",
        },
        {
            "name": "Web — EAN pool",
            "description": "Manager EAN import, allocation overview and list.",
        },
        {
            "name": "Web — Manager accounts",
            "description": "Create manager accounts from the protected web panel.",
        },
        {
            "name": "Notifications",
            "description": "In-app notifications and Firebase device tokens.",
        },
    ],
}

SPECTACULAR_SETTINGS["TAGS"] = [
    {
        "name": "Auth - Seller",
        "description": "Регистрация продавца, подтверждение email и профиль.",
    },
    {
        "name": "Auth - Shared",
        "description": "Общий JWT-вход, обновление и выход для web и mobile.",
    },
    {
        "name": "Manager accounts",
        "description": "Создание менеджеров из защищённой web-панели.",
    },
    {
        "name": "Products",
        "description": "Товары, варианты, цены и жизненный цикл продавца.",
    },
    {
        "name": "Product images",
        "description": "Загрузка, сортировка, главное фото и AI-обработка.",
    },
    {
        "name": "Moderation",
        "description": "Отправка товара продавцом и история решений.",
    },
    {
        "name": "Manager moderation",
        "description": "Проверка, одобрение, отклонение и сообщения менеджера.",
    },
    {
        "name": "EAN pool",
        "description": "Импорт, остаток и просмотр EAN-пула JV/XL.",
    },
    {
        "name": "Notifications",
        "description": "Уведомления в приложении и регистрация FCM-устройств.",
    },
    {
        "name": "OTTO - Catalog",
        "description": "Локальный каталог групп, категорий и атрибутов OTTO.",
    },
    {
        "name": "OTTO - Delivery",
        "description": "Доступные для менеджера профили доставки JV и XL.",
    },
    {
        "name": "AI content",
        "description": "Асинхронная генерация и применение контента для маркетплейсов.",
    },
    {
        "name": "OTTO",
        "description": "Конфигурация и предпросмотр payload для OTTO.",
    },
    {
        "name": "Hood",
        "description": "Конфигурация и предпросмотр payload для Hood.",
    },
    {
        "name": "Kaufland",
        "description": "Конфигурация и предпросмотр payload для Kaufland.",
    },
    {
        "name": "Marketplace jobs",
        "description": "Очереди публикации, обновления, поиска и смены состояния листингов.",
    },
]

CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
CORS_ALLOW_CREDENTIALS = True


CELERY_BROKER_URL = env("CELERY_BROKER_URL")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND")

REDIS_CACHE_URL = env(
    "REDIS_CACHE_URL",
    default="redis://127.0.0.1:6380/2",
)

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_CACHE_URL,
        "TIMEOUT": 300,
        "OPTIONS": {
            "socket_connect_timeout": 2,
            "socket_timeout": 2,
        },
    },
}

CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"

CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
# Per-task limits below are deliberately longer than the provider HTTP timeout.
# The previous global 120 seconds could kill valid multi-target jobs mid-flight.
CELERY_TASK_TIME_LIMIT = 330
CELERY_TASK_SOFT_TIME_LIMIT = 300
CELERY_RESULT_EXPIRES = 86400
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
# Один worker берёт только одну задачу за раз: длинная AI-задача
# не будет заранее резервировать очередь целиком.
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
# Дольше максимального времени любой Celery-задачи.
# Redis вернёт невыполненную задачу в очередь, если worker умрёт.
CELERY_BROKER_TRANSPORT_OPTIONS = {
    "visibility_timeout": 3600,
}
CELERY_TASK_DEFAULT_QUEUE = "maintenance"
CELERY_TASK_QUEUES = (
    Queue("marketplace", Exchange("marketplace", type="direct"), "marketplace"),
    Queue("ai", Exchange("ai", type="direct"), "ai"),
    Queue("images", Exchange("images", type="direct"), "images"),
    Queue("notifications", Exchange("notifications", type="direct"), "notifications"),
    Queue("maintenance", Exchange("maintenance", type="direct"), "maintenance"),
)
CELERY_TASK_ROUTES = {
    "apps.orchestrator.tasks.execute_marketplace_job": {"queue": "marketplace"},
    "apps.orchestrator.tasks.check_otto_publication_process": {"queue": "marketplace"},
    "apps.orchestrator.tasks.check_otto_marketplace_status": {"queue": "marketplace"},
    "apps.orchestrator.tasks.generate_marketplace_content": {"queue": "ai"},
    "apps.notifications.tasks.process_product_image": {"queue": "images"},
    "apps.notifications.tasks.check_product_image_generation": {"queue": "images"},
    "apps.notifications.tasks.send_notification_push": {"queue": "notifications"},
    "apps.notifications.tasks.send_product_availability_reminders": {
        "queue": "notifications"
    },
    "apps.orchestrator.tasks.recover_stale_orchestrator_jobs": {"queue": "maintenance"},
    "apps.idempotency.tasks.purge_expired_idempotency_records": {
        "queue": "maintenance"
    },
    "apps.notifications.tasks.recover_stale_product_image_processing": {
        "queue": "maintenance",
    },
    "apps.notifications.tasks.recover_stale_push_deliveries": {
        "queue": "maintenance",
    },
}
CELERY_TASK_ANNOTATIONS = {
    "apps.orchestrator.tasks.execute_marketplace_job": {
        "soft_time_limit": 240,
        "time_limit": 270,
    },
    "apps.orchestrator.tasks.generate_marketplace_content": {
        "soft_time_limit": 270,
        "time_limit": 300,
    },
    "apps.notifications.tasks.process_product_image": {
        "soft_time_limit": 180,
        "time_limit": 210,
    },
    "apps.notifications.tasks.check_product_image_generation": {
        "soft_time_limit": 540,
        "time_limit": 570,
    },
    "apps.notifications.tasks.send_notification_push": {
        "soft_time_limit": 60,
        "time_limit": 90,
    },
}
ORCHESTRATOR_STALE_JOB_MINUTES = env.int(
    "ORCHESTRATOR_STALE_JOB_MINUTES",
    default=20,
)
AI_CONTENT_STALE_JOB_MINUTES = env.int(
    "AI_CONTENT_STALE_JOB_MINUTES",
    default=15,
)

# Direct marketplace API configuration. BENIM talks to these providers itself;
# no WareHub service is required at runtime.
# Marketplace API configuration.
HOOD_API_BASE_URL = env(
    "HOOD_API_BASE_URL",
    default="https://hoodbot.automatonsoft.de/api",
).rstrip("/")
HOOD_API_GET_ENDPOINT = env(
    "HOOD_API_GET_ENDPOINT",
    default="/items/by-ean/{ean}",
)
HOOD_API_PATCH_ENDPOINT = env(
    "HOOD_API_PATCH_ENDPOINT",
    default="/items/by-ean/{ean}",
)
HOOD_API_CREATE_ENDPOINT = env(
    "HOOD_API_CREATE_ENDPOINT",
    default="/items/by-ean/{ean}",
)
HOOD_API_DELETE_ENDPOINT = env(
    "HOOD_API_DELETE_ENDPOINT",
    default="/items/delete/by-item-number/{ean}",
)
HOOD_LOGIN = env("HOOD_LOGIN", default="")
HOOD_PASSWORD = env("HOOD_PASSWORD", default="")

KAUFLAND_API_BASE_URL = env(
    "KAUFLAND_API_BASE_URL",
    default="https://kl.automatonsoft.de",
).rstrip("/")
KAUFLAND_API_GET_BY_EAN_ENDPOINT = env(
    "KAUFLAND_API_GET_BY_EAN_ENDPOINT",
    default="/api/products/product/{ean}/",
)
KAUFLAND_API_CREATE_ENDPOINT = env(
    "KAUFLAND_API_CREATE_ENDPOINT",
    default="/api/products/upload/",
)
KAUFLAND_API_UPDATE_ENDPOINT = env(
    "KAUFLAND_API_UPDATE_ENDPOINT",
    default="/api/products/{ean}/change/",
)
KAUFLAND_API_DELETE_ENDPOINT = env(
    "KAUFLAND_API_DELETE_ENDPOINT",
    default="/api/products/delete/{ean}",
)

OTTO_API_BASE_URL = env(
    "OTTO_API_BASE_URL",
    default="https://okb.automatonsoft.de",
).rstrip("/")
OTTO_API_PRODUCTS_ENDPOINT = env(
    "OTTO_API_PRODUCTS_ENDPOINT",
    default="/extermal/get_products",
)
OTTO_API_UPSERT_ENDPOINT = env(
    "OTTO_API_UPSERT_ENDPOINT",
    default="/extermal/create_or_update_product",
)
OTTO_API_ACTIVATE_ENDPOINT = env(
    "OTTO_API_ACTIVATE_ENDPOINT",
    default="/extermal/activate",
)
OTTO_API_DEACTIVATE_ENDPOINT = env(
    "OTTO_API_DEACTIVATE_ENDPOINT",
    default="/extermal/deactivate",
)
OTTO_API_PROCESS_FAILED_ENDPOINT = env(
    "OTTO_API_PROCESS_FAILED_ENDPOINT",
    default="/v1/products/otto/update-tasks/{process_id}/failed",
)
OTTO_API_PROCESS_SUCCESS_ENDPOINT = env(
    "OTTO_API_PROCESS_SUCCESS_ENDPOINT",
    default="/v1/products/otto/update-tasks/{process_id}/succeeded",
)
OTTO_API_PROCESS_UNCHANGED_ENDPOINT = env(
    "OTTO_API_PROCESS_UNCHANGED_ENDPOINT",
    default="/v1/products/otto/update-tasks/{process_id}/unchanged",
)
OTTO_PROCESS_POLL_INTERVAL_SECONDS = env.int(
    "OTTO_PROCESS_POLL_INTERVAL_SECONDS",
    default=30,
)
OTTO_PROCESS_MAX_POLL_ATTEMPTS = env.int(
    "OTTO_PROCESS_MAX_POLL_ATTEMPTS",
    default=120,
)

MARKETPLACE_HTTP_CONNECT_TIMEOUT_SECONDS = env.int(
    "MARKETPLACE_HTTP_CONNECT_TIMEOUT_SECONDS",
    default=5,
)

MARKETPLACE_HTTP_READ_TIMEOUT_SECONDS = env.int(
    "MARKETPLACE_HTTP_READ_TIMEOUT_SECONDS",
    default=25,
)

MARKETPLACE_HTTP_RETRY_TOTAL = env.int(
    "MARKETPLACE_HTTP_RETRY_TOTAL",
    default=3,
)

MARKETPLACE_HTTP_RETRY_BACKOFF_FACTOR = env.float(
    "MARKETPLACE_HTTP_RETRY_BACKOFF_FACTOR",
    default=0.5,
)

OTTO_API_MARKETPLACE_STATUS_ENDPOINT = env(
    "OTTO_API_MARKETPLACE_STATUS_ENDPOINT",
    default="/v1/products/marketplace_status",
)

# Проверяем реальную публикацию OTTO раз в 5 минут, максимум 24 часа.
OTTO_MARKETPLACE_STATUS_POLL_INTERVAL_SECONDS = env.int(
    "OTTO_MARKETPLACE_STATUS_POLL_INTERVAL_SECONDS",
    default=300,
)

OTTO_MARKETPLACE_STATUS_MAX_POLL_ATTEMPTS = env.int(
    "OTTO_MARKETPLACE_STATUS_MAX_POLL_ATTEMPTS",
    default=288,
)

PRODUCT_AVAILABILITY_REMINDER_DAYS = env.int(
    "PRODUCT_AVAILABILITY_REMINDER_DAYS",
    default=14,
)
PRODUCT_AVAILABILITY_REMINDER_BATCH_SIZE = env.int(
    "PRODUCT_AVAILABILITY_REMINDER_BATCH_SIZE",
    default=500,
)
OPENAI_ENABLED = env.bool(
    "OPENAI_ENABLED",
    default=False,
)

OPENAI_API_KEY = env(
    "OPENAI_API_KEY",
    default="",
)

OPENAI_TEXT_MODELS = tuple(
    model.strip()
    for model in env(
        "OPENAI_TEXT_MODELS",
        default="",
    ).split(",")
    if model.strip()
)

OPENAI_TEXT_TIMEOUT_SECONDS = env.float(
    "OPENAI_TEXT_TIMEOUT_SECONDS",
    default=60,
)

OPENAI_TEXT_MAX_RETRIES_PER_MODEL = env.int(
    "OPENAI_TEXT_MAX_RETRIES_PER_MODEL",
    default=2,
)

OPENAI_TEXT_RETRY_DELAY_SECONDS = env.float(
    "OPENAI_TEXT_RETRY_DELAY_SECONDS",
    default=1.5,
)

CELERY_BEAT_SCHEDULE = {
    "send-product-availability-reminders-daily": {
        "task": ("apps.notifications.tasks.send_product_availability_reminders"),
        "schedule": crontab(hour=10, minute=0),
    },
    "recover-stale-orchestrator-jobs": {
        "task": "apps.orchestrator.tasks.recover_stale_orchestrator_jobs",
        "schedule": crontab(minute="*/10"),
    },
    "idempotency-cleanup-hourly": {
        "task": "apps.idempotency.tasks.purge_expired_idempotency_records",
        "schedule": crontab(minute=25),
    },
    "recover-stale-product-image-processing": {
        "task": ("apps.notifications.tasks.recover_stale_product_image_processing"),
        "schedule": crontab(minute="*/10"),
    },
    "recover-stale-push-deliveries": {
        "task": "apps.notifications.tasks.recover_stale_push_deliveries",
        "schedule": crontab(minute="*/5"),
    },
}

PUSH_NOTIFICATION_MAX_ATTEMPTS = env.int(
    "PUSH_NOTIFICATION_MAX_ATTEMPTS",
    default=5,
)
PUSH_NOTIFICATION_RETRY_BASE_SECONDS = env.int(
    "PUSH_NOTIFICATION_RETRY_BASE_SECONDS",
    default=30,
)
PUSH_NOTIFICATION_RETRY_MAX_SECONDS = env.int(
    "PUSH_NOTIFICATION_RETRY_MAX_SECONDS",
    default=900,
)
PUSH_DELIVERY_PROCESSING_LEASE_SECONDS = env.int(
    "PUSH_DELIVERY_PROCESSING_LEASE_SECONDS",
    default=120,
)
PUSH_DELIVERY_RECOVERY_BATCH_SIZE = env.int(
    "PUSH_DELIVERY_RECOVERY_BATCH_SIZE",
    default=500,
)

IDEMPOTENCY_TTL_HOURS = env.int("IDEMPOTENCY_TTL_HOURS", default=24)
IDEMPOTENCY_CLEANUP_BATCH_SIZE = env.int(
    "IDEMPOTENCY_CLEANUP_BATCH_SIZE",
    default=5000,
)

IMAGE_PROCESSING_LEASE_SECONDS = env.int(
    "IMAGE_PROCESSING_LEASE_SECONDS",
    default=600,
)

IMAGE_PROCESSING_RECOVERY_BATCH_SIZE = env.int(
    "IMAGE_PROCESSING_RECOVERY_BATCH_SIZE",
    default=100,
)

FIREBASE_ENABLED = env.bool("FIREBASE_ENABLED", default=False)

FIREBASE_SERVICE_ACCOUNT_FILE = env(
    "FIREBASE_SERVICE_ACCOUNT_FILE",
    default="",
)

BULK_WHITE_IMAGE_SERVICE_URL = env(
    "BULK_WHITE_IMAGE_SERVICE_URL",
    default="",
)
BULK_WHITE_IMAGE_SERVICE_TOKEN = env(
    "BULK_WHITE_IMAGE_SERVICE_TOKEN",
    default="",
)
BULK_WHITE_IMAGE_SERVICE_TIMEOUT_SECONDS = env.int(
    "BULK_WHITE_IMAGE_SERVICE_TIMEOUT_SECONDS",
    default=120,
)
BULK_WHITE_IMAGE_SERVICE_RESULTS_URL = env(
    "BULK_WHITE_IMAGE_SERVICE_RESULTS_URL",
    default="",
)
BULK_WHITE_IMAGE_SERVICE_POLL_INTERVAL_SECONDS = env.int(
    "BULK_WHITE_IMAGE_SERVICE_POLL_INTERVAL_SECONDS",
    default=10,
)
BULK_WHITE_IMAGE_SERVICE_MAX_POLL_ATTEMPTS = env.int(
    "BULK_WHITE_IMAGE_SERVICE_MAX_POLL_ATTEMPTS",
    default=60,
)
EAN_LOW_STOCK_THRESHOLD = env.int("EAN_LOW_STOCK_THRESHOLD", default=20)

EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    default="django.core.mail.backends.smtp.EmailBackend",
)
EMAIL_HOST = env("EMAIL_HOST")
EMAIL_PORT = env.int("EMAIL_PORT", default=465)
EMAIL_USE_SSL = env.bool("EMAIL_USE_SSL", default=True)
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=False)
EMAIL_HOST_USER = env("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL")
EMAIL_TIMEOUT = env.int("EMAIL_TIMEOUT", default=15)

EMAIL_VERIFICATION_CODE_TTL_MINUTES = env.int(
    "EMAIL_VERIFICATION_CODE_TTL_MINUTES",
    default=10,
)
EMAIL_VERIFICATION_MAX_ATTEMPTS = env.int(
    "EMAIL_VERIFICATION_MAX_ATTEMPTS",
    default=5,
)
EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS = env.int(
    "EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS",
    default=60,
)

BULK_WHITE_IMAGE_ALLOWED_IMAGE_HOSTS = tuple(
    host.strip().lower()
    for host in env.list(
        "BULK_WHITE_IMAGE_ALLOWED_IMAGE_HOSTS",
        default=[],
    )
    if host.strip()
)

BULK_WHITE_IMAGE_MAX_DOWNLOAD_BYTES = env.int(
    "BULK_WHITE_IMAGE_MAX_DOWNLOAD_BYTES",
    default=15 * 1024 * 1024,
)

PRODUCT_IMAGE_MAX_UPLOAD_BYTES = env.int(
    "PRODUCT_IMAGE_MAX_UPLOAD_BYTES",
    default=10 * 1024 * 1024,
)

PRODUCT_IMAGE_MAX_PIXELS = env.int(
    "PRODUCT_IMAGE_MAX_PIXELS",
    default=25_000_000,
)


EXTERNAL_JSON_MAX_BYTES = env.int(
    "EXTERNAL_JSON_MAX_BYTES",
    default=64 * 1024,
)

EXTERNAL_JSON_MAX_DEPTH = env.int(
    "EXTERNAL_JSON_MAX_DEPTH",
    default=8,
)

EXTERNAL_JSON_MAX_ITEMS_PER_CONTAINER = env.int(
    "EXTERNAL_JSON_MAX_ITEMS_PER_CONTAINER",
    default=100,
)

EXTERNAL_JSON_MAX_STRING_CHARS = env.int(
    "EXTERNAL_JSON_MAX_STRING_CHARS",
    default=4000,
)
