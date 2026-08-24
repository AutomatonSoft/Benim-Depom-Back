from datetime import timedelta

from .base import *  # noqa: F403

DEBUG = env.bool("DJANGO_DEBUG", default=True)

SIMPLE_JWT = {
    **SIMPLE_JWT,
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=1),
}
