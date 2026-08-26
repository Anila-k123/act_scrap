"""Minimal, headless Django settings — ORM + migrations only.

No REST framework, no channels, no middleware, no HTTP server. This project
is never run with `runserver`; it only ever runs management commands
(`import_acts`, `spike_fetch_act`) manually or via a scheduled task, writing
into the SAME Postgres database (advocate_db) the `ams` Django app reads
from. The two codebases are deliberately separate — see README.md.
"""

from pathlib import Path
from decouple import config

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config('SECRET_KEY', default='acts-importer-not-a-web-server')
DEBUG = config('DEBUG', default=True, cast=bool)

INSTALLED_APPS = [
    'django.contrib.contenttypes',
    'acts',
]

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': config('DB_NAME', default='advocate_db'),
        'USER': config('DB_USER', default='postgres'),
        'PASSWORD': config('DB_PASSWORD', default='psql_password'),
        'HOST': config('DB_HOST', default='localhost'),
        'PORT': config('DB_PORT', default='5432'),
    }
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
USE_TZ = True
