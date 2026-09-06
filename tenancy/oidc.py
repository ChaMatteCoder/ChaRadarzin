from __future__ import annotations

from authlib.integrations.django_client import OAuth
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


oauth = OAuth()


def get_telegram_client():
    if not settings.TELEGRAM_OIDC_ENABLED:
        raise ImproperlyConfigured("Telegram OIDC nao esta habilitado.")
    client = oauth.create_client("telegram")
    if client is None:
        oauth.register(
            name="telegram",
            client_id=settings.TELEGRAM_OIDC_CLIENT_ID,
            client_secret=settings.TELEGRAM_OIDC_CLIENT_SECRET,
            server_metadata_url=settings.TELEGRAM_OIDC_METADATA_URL,
            client_kwargs={
                "scope": "openid profile",
                "code_challenge_method": "S256",
            },
        )
        client = oauth.create_client("telegram")
    return client
