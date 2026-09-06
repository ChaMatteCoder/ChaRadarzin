from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlparse
import uuid

import dj_database_url
from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured


BASE_DIR = Path(__file__).resolve().parent.parent


def _env_file_values(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


_FILE_ENV = _env_file_values(BASE_DIR / ".env")


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, _FILE_ENV.get(name, default)).strip()


def env_bool(name: str, default: bool = False) -> bool:
    fallback = "true" if default else "false"
    return env(name, fallback).casefold() in {"1", "true", "sim", "yes", "on"}


APP_ENV = env("CHADARADZIN_ENV", "development").casefold()
DEPLOYMENT_ENVIRONMENTS = {"staging", "production"}
IS_DEPLOYMENT = APP_ENV in DEPLOYMENT_ENVIRONMENTS
DEBUG = env_bool("DJANGO_DEBUG", not IS_DEPLOYMENT)
SECRET_KEY = env("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if IS_DEPLOYMENT or not DEBUG:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY e obrigatoria em homologacao e producao"
        )
    SECRET_KEY = "development-only-change-me"

raw_hosts = env("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")
ALLOWED_HOSTS = [host.strip() for host in raw_hosts.split(",") if host.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "tenancy.apps.TenancyConfig",
    "monitoring.apps.MonitoringConfig",
    "telegram_bots.apps.TelegramBotsConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "chadaradzin.urls"
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
    }
]
WSGI_APPLICATION = "chadaradzin.wsgi.application"
ASGI_APPLICATION = "chadaradzin.asgi.application"

database_url = env("DATABASE_URL")
if database_url:
    DATABASES = {
        "default": dj_database_url.parse(
            database_url,
            conn_max_age=60,
            conn_health_checks=True,
        )
    }
    if IS_DEPLOYMENT and "postgresql" not in DATABASES["default"]["ENGINE"]:
        raise ImproperlyConfigured(
            "Homologacao e producao exigem uma DATABASE_URL PostgreSQL."
        )
elif IS_DEPLOYMENT:
    raise ImproperlyConfigured(
        "DATABASE_URL PostgreSQL e obrigatoria em homologacao e producao"
    )
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "data" / "chadaradzin_web.sqlite3",
        }
    }

AUTH_USER_MODEL = "tenancy.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "whitenoise.storage.CompressedManifestStaticFilesStorage"
            if IS_DEPLOYMENT
            else "django.contrib.staticfiles.storage.StaticFilesStorage"
        )
    },
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "landing"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "landing"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
}

STRUCTURED_LOGS = env_bool("STRUCTURED_LOGS", True)
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {"()": "chadaradzin.logging.JsonFormatter"},
        "plain": {"format": "%(asctime)s | %(levelname)s | %(name)s | %(message)s"},
    },
    "loggers": {
        # Authlib can describe PKCE internals at DEBUG. Production and local logs
        # do not need those transient authentication values.
        "authlib": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "requests": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "urllib3": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "chadaradzin": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "tenancy": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "monitoring": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "telegram_bots": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json" if STRUCTURED_LOGS else "plain",
        },
    },
}

TELEGRAM_OIDC_ENABLED = env_bool("TELEGRAM_OIDC_ENABLED", False)
BETA_ACCESS_REQUIRED = env_bool("BETA_ACCESS_REQUIRED", IS_DEPLOYMENT)
TELEGRAM_OIDC_CLIENT_ID = env("TELEGRAM_OIDC_CLIENT_ID")
TELEGRAM_OIDC_CLIENT_SECRET = env("TELEGRAM_OIDC_CLIENT_SECRET")
TELEGRAM_OIDC_ISSUER = "https://oauth.telegram.org"
TELEGRAM_OIDC_METADATA_URL = (
    "https://oauth.telegram.org/.well-known/openid-configuration"
)
if TELEGRAM_OIDC_ENABLED and (
    not TELEGRAM_OIDC_CLIENT_ID or not TELEGRAM_OIDC_CLIENT_SECRET
):
    raise ImproperlyConfigured(
        "Telegram OIDC habilitado exige TELEGRAM_OIDC_CLIENT_ID e "
        "TELEGRAM_OIDC_CLIENT_SECRET"
    )

PUBLIC_BASE_URL = env("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
TELEGRAM_MANAGER_ENABLED = env_bool("TELEGRAM_MANAGER_ENABLED", False)
TELEGRAM_MANAGER_BOT_TOKEN = env("TELEGRAM_MANAGER_BOT_TOKEN")
TELEGRAM_MANAGER_BOT_USERNAME = env("TELEGRAM_MANAGER_BOT_USERNAME").lstrip("@")
TELEGRAM_MANAGER_WEBHOOK_ID = env("TELEGRAM_MANAGER_WEBHOOK_ID")
TELEGRAM_MANAGER_WEBHOOK_SECRET = env("TELEGRAM_MANAGER_WEBHOOK_SECRET")
BOT_TOKEN_ENCRYPTION_KEYS = env("BOT_TOKEN_ENCRYPTION_KEYS")
TELEGRAM_FALLBACK_BOT_TOKEN = env("TELEGRAM_FALLBACK_BOT_TOKEN")
TELEGRAM_ONBOARDING_TTL_MINUTES = int(env("TELEGRAM_ONBOARDING_TTL_MINUTES", "20"))
TELEGRAM_MANUAL_REFRESH_COOLDOWN_MINUTES = int(
    env("TELEGRAM_MANUAL_REFRESH_COOLDOWN_MINUTES", "60")
)
TELEGRAM_API_TIMEOUT_SECONDS = float(env("TELEGRAM_API_TIMEOUT_SECONDS", "8"))
TELEGRAM_DELIVERY_LEASE_SECONDS = int(env("TELEGRAM_DELIVERY_LEASE_SECONDS", "300"))
TELEGRAM_WEBHOOK_RATE_LIMIT = int(env("TELEGRAM_WEBHOOK_RATE_LIMIT", "120"))
TELEGRAM_WEBHOOK_RATE_WINDOW_SECONDS = int(
    env("TELEGRAM_WEBHOOK_RATE_WINDOW_SECONDS", "60")
)
DATA_UPLOAD_MAX_MEMORY_SIZE = 1_048_576

PRODUCT_PREVIEW_TTL_MINUTES = int(env("PRODUCT_PREVIEW_TTL_MINUTES", "30"))
PRODUCT_PREVIEW_RATE_LIMIT = int(env("PRODUCT_PREVIEW_RATE_LIMIT", "8"))
PRODUCT_PREVIEW_WINDOW_MINUTES = int(env("PRODUCT_PREVIEW_WINDOW_MINUTES", "10"))
PRODUCT_PREVIEW_TIMEOUT_SECONDS = float(env("PRODUCT_PREVIEW_TIMEOUT_SECONDS", "8"))
PRODUCT_PREVIEW_MAX_BYTES = int(env("PRODUCT_PREVIEW_MAX_BYTES", "2097152"))
COLLECTION_REQUESTS_PER_MINUTE = int(env("COLLECTION_REQUESTS_PER_MINUTE", "12"))
COLLECTION_TIMEOUT_SECONDS = float(env("COLLECTION_TIMEOUT_SECONDS", "10"))
COLLECTION_MAX_BYTES = int(env("COLLECTION_MAX_BYTES", "2097152"))
COLLECTION_SHIPPING_TIMEOUT_SECONDS = float(
    env("COLLECTION_SHIPPING_TIMEOUT_SECONDS", "8")
)
COLLECTION_DAILY_TIME = env("COLLECTION_DAILY_TIME", "21:05")
COLLECTION_JOB_MAX_ATTEMPTS = int(env("COLLECTION_JOB_MAX_ATTEMPTS", "4"))
COLLECTION_RETRY_BASE_SECONDS = int(env("COLLECTION_RETRY_BASE_SECONDS", "300"))
COLLECTION_RETRY_MAX_SECONDS = int(env("COLLECTION_RETRY_MAX_SECONDS", "3600"))
COLLECTION_JOB_LEASE_SECONDS = int(env("COLLECTION_JOB_LEASE_SECONDS", "7200"))
COLLECTION_WORKER_POLL_SECONDS = float(env("COLLECTION_WORKER_POLL_SECONDS", "5"))
COLLECTION_SCHEDULER_POLL_SECONDS = float(
    env("COLLECTION_SCHEDULER_POLL_SECONDS", "60")
)
OPERATIONAL_LOOKBACK_HOURS = int(env("OPERATIONAL_LOOKBACK_HOURS", "24"))
OPERATIONAL_COLLECTION_STALE_HOURS = int(
    env("OPERATIONAL_COLLECTION_STALE_HOURS", "30")
)
OPERATIONAL_MONITOR_INTERVAL_SECONDS = int(
    env("OPERATIONAL_MONITOR_INTERVAL_SECONDS", "300")
)
TRANSIENT_DATA_RETENTION_DAYS = int(env("TRANSIENT_DATA_RETENTION_DAYS", "30"))
SECURITY_EVENT_RETENTION_DAYS = int(env("SECURITY_EVENT_RETENTION_DAYS", "180"))
BACKUP_RETENTION_DAYS = int(env("BACKUP_RETENTION_DAYS", "30"))

if TELEGRAM_ONBOARDING_TTL_MINUTES < 1 or TELEGRAM_MANUAL_REFRESH_COOLDOWN_MINUTES < 1:
    raise ImproperlyConfigured("Os prazos do onboarding e do refresh devem ser positivos.")
if min(
    TELEGRAM_DELIVERY_LEASE_SECONDS,
    TELEGRAM_WEBHOOK_RATE_LIMIT,
    TELEGRAM_WEBHOOK_RATE_WINDOW_SECONDS,
) < 1 or TELEGRAM_API_TIMEOUT_SECONDS <= 0:
    raise ImproperlyConfigured(
        "Timeout, lease e rate limit do Telegram devem ser positivos."
    )
if min(
    PRODUCT_PREVIEW_TTL_MINUTES,
    PRODUCT_PREVIEW_RATE_LIMIT,
    PRODUCT_PREVIEW_WINDOW_MINUTES,
    PRODUCT_PREVIEW_MAX_BYTES,
) < 1 or PRODUCT_PREVIEW_TIMEOUT_SECONDS <= 0:
    raise ImproperlyConfigured("Os limites da previa de produto devem ser positivos.")
if min(COLLECTION_REQUESTS_PER_MINUTE, COLLECTION_MAX_BYTES) < 1 or min(
    COLLECTION_TIMEOUT_SECONDS,
    COLLECTION_SHIPPING_TIMEOUT_SECONDS,
) <= 0:
    raise ImproperlyConfigured("Os limites do motor de coleta devem ser positivos.")
if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", COLLECTION_DAILY_TIME):
    raise ImproperlyConfigured("COLLECTION_DAILY_TIME deve usar o formato HH:MM.")
if min(
    COLLECTION_JOB_MAX_ATTEMPTS,
    COLLECTION_RETRY_BASE_SECONDS,
    COLLECTION_RETRY_MAX_SECONDS,
    COLLECTION_JOB_LEASE_SECONDS,
) < 1 or COLLECTION_WORKER_POLL_SECONDS <= 0:
    raise ImproperlyConfigured("Os limites da fila de coleta devem ser positivos.")
if COLLECTION_RETRY_MAX_SECONDS < COLLECTION_RETRY_BASE_SECONDS:
    raise ImproperlyConfigured(
        "COLLECTION_RETRY_MAX_SECONDS nao pode ser menor que o backoff inicial."
    )
if min(
    OPERATIONAL_LOOKBACK_HOURS,
    OPERATIONAL_COLLECTION_STALE_HOURS,
    OPERATIONAL_MONITOR_INTERVAL_SECONDS,
) < 1:
    raise ImproperlyConfigured("As janelas operacionais devem ser positivas.")
if COLLECTION_SCHEDULER_POLL_SECONDS <= 0:
    raise ImproperlyConfigured("O intervalo do scheduler deve ser positivo.")
if min(
    TRANSIENT_DATA_RETENTION_DAYS,
    SECURITY_EVENT_RETENTION_DAYS,
    BACKUP_RETENTION_DAYS,
) < 1:
    raise ImproperlyConfigured("As retencoes operacionais devem ser positivas.")

if TELEGRAM_MANAGER_ENABLED:
    manager_requirements = {
        "TELEGRAM_MANAGER_BOT_TOKEN": TELEGRAM_MANAGER_BOT_TOKEN,
        "TELEGRAM_MANAGER_BOT_USERNAME": TELEGRAM_MANAGER_BOT_USERNAME,
        "TELEGRAM_MANAGER_WEBHOOK_ID": TELEGRAM_MANAGER_WEBHOOK_ID,
        "TELEGRAM_MANAGER_WEBHOOK_SECRET": TELEGRAM_MANAGER_WEBHOOK_SECRET,
        "BOT_TOKEN_ENCRYPTION_KEYS": BOT_TOKEN_ENCRYPTION_KEYS,
    }
    missing_manager_settings = [
        name for name, value in manager_requirements.items() if not value
    ]
    if missing_manager_settings:
        raise ImproperlyConfigured(
            "Manager bot habilitado exige: " + ", ".join(missing_manager_settings)
        )
    try:
        uuid.UUID(TELEGRAM_MANAGER_WEBHOOK_ID)
    except ValueError as exc:
        raise ImproperlyConfigured(
            "TELEGRAM_MANAGER_WEBHOOK_ID deve ser um UUID opaco."
        ) from exc
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", TELEGRAM_MANAGER_WEBHOOK_SECRET):
        raise ImproperlyConfigured(
            "TELEGRAM_MANAGER_WEBHOOK_SECRET deve usar somente letras, numeros, _ ou -."
        )
    try:
        encryption_entries = [entry.strip() for entry in BOT_TOKEN_ENCRYPTION_KEYS.split(",") if entry.strip()]
        if not encryption_entries:
            raise ValueError
        versions = set()
        for entry in encryption_entries:
            raw_version, raw_key = entry.split(":", 1)
            version = int(raw_version)
            if version <= 0 or version in versions:
                raise ValueError
            Fernet(raw_key.encode("ascii"))
            versions.add(version)
    except (ValueError, UnicodeEncodeError) as exc:
        raise ImproperlyConfigured(
            "BOT_TOKEN_ENCRYPTION_KEYS deve usar versao:chave_fernet valida."
        ) from exc
    public_url = urlparse(PUBLIC_BASE_URL)
    if not DEBUG and (
        public_url.scheme != "https" or not public_url.netloc
    ):
        raise ImproperlyConfigured("PUBLIC_BASE_URL deve usar HTTPS em producao.")

CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_SAMESITE = "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", not DEBUG)
SECURE_HSTS_SECONDS = 0 if DEBUG else 31_536_000
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG

if env_bool("DJANGO_TRUST_PROXY_HEADERS", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

raw_csrf_origins = env("DJANGO_CSRF_TRUSTED_ORIGINS")
CSRF_TRUSTED_ORIGINS = [
    origin.strip() for origin in raw_csrf_origins.split(",") if origin.strip()
]
