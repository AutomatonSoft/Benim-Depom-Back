from datetime import timedelta

from .base import *  # noqa: F403

DEBUG = env.bool("DJANGO_DEBUG", default=True)

# Next's local proxy preserves the browser's host in X-Forwarded-Host.
USE_X_FORWARDED_HOST = True

SIMPLE_JWT = {
    **SIMPLE_JWT,
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=1),
}
