from __future__ import annotations

from django.contrib import admin
from django.urls import include, path

from chadaradzin import health
from tenancy.views import (
    delete_account,
    privacy_policy,
    telegram_callback,
    telegram_login,
    user_logout,
)


urlpatterns = [
    path("health/live/", health.live, name="health-live"),
    path("health/ready/", health.ready, name="health-ready"),
    path("", include("monitoring.urls")),
    path("auth/telegram/", telegram_login, name="telegram-login"),
    path("auth/telegram/callback/", telegram_callback, name="telegram-callback"),
    path("auth/sair/", user_logout, name="logout"),
    path("privacidade/", privacy_policy, name="privacy-policy"),
    path("conta/excluir/", delete_account, name="delete-account"),
    path("api/v1/", include("monitoring.api_urls")),
    path("", include("telegram_bots.urls")),
    path("admin/", admin.site.urls),
]
