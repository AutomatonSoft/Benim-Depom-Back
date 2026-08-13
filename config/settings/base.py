from datetime import timedelta
from pathlib import Path
from celery.schedules import crontab

import environ

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
        "DIRS": [],
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
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
        ),
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
        {
            "name": "Service",
            "description": "Service health checks.",
        },
    ],
}

CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
CORS_ALLOW_CREDENTIALS = True


CELERY_BROKER_URL = env("CELERY_BROKER_URL")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND")

CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"

CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 120
CELERY_TASK_SOFT_TIME_LIMIT = 110
CELERY_RESULT_EXPIRES = 86400
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True




PRODUCT_AVAILABILITY_REMINDER_DAYS = env.int(
    "PRODUCT_AVAILABILITY_REMINDER_DAYS",
    default=14,
)
PRODUCT_AVAILABILITY_REMINDER_BATCH_SIZE = env.int(
    "PRODUCT_AVAILABILITY_REMINDER_BATCH_SIZE",
    default=500,
)

CELERY_BEAT_SCHEDULE = {
    "send-product-availability-reminders-daily": {
        "task": (
            "apps.notifications.tasks."
            "send_product_availability_reminders"
        ),
        "schedule": crontab(hour=10, minute=0),
    },
}

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
