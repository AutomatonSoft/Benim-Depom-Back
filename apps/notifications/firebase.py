from functools import lru_cache
from pathlib import Path

import firebase_admin
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from firebase_admin import credentials


@lru_cache
def get_firebase_app():
    if not settings.FIREBASE_ENABLED:
        return None

    credential_path = Path(settings.FIREBASE_SERVICE_ACCOUNT_FILE)

    if not settings.FIREBASE_SERVICE_ACCOUNT_FILE:
        raise ImproperlyConfigured("FIREBASE_SERVICE_ACCOUNT_FILE is not configured")

    if not credential_path.is_absolute():
        credential_path = settings.BASE_DIR / credential_path

    if not credential_path.is_file():
        raise ImproperlyConfigured("Firebase service-account JSON file was not found.")

    try:
        return firebase_admin.get_app()
    except ValueError:
        credential = credentials.Certificate(credential_path)
        return firebase_admin.initialize_app(credential)
