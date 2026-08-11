from .local import *  # noqa: F403


PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.InMemoryStorage",
    },
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
        ),
    },
}
