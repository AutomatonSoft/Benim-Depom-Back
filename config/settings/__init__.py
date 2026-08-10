import os
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parents[2]

environ.Env.read_env(BASE_DIR / ".env")

environment = os.environ.get("DJANGO_ENV", "local")

if environment == "production":
    from .production import *  # noqa: F403
else:
    from .local import *  # noqa: F403