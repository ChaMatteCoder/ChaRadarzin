from __future__ import annotations

import logging
from collections.abc import Mapping

from authlib.integrations.base_client.errors import OAuthError
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from tenancy.oidc import get_telegram_client
from tenancy.services import (
    TelegramClaims,
    TelegramIdentityError,
    login_or_create_telegram_user,
)
from tenancy.privacy import delete_personal_account


LOGGER = logging.getLogger(__name__)


@require_GET
def telegram_login(request: HttpRequest) -> HttpResponse:
    if not settings.TELEGRAM_OIDC_ENABLED:
        return HttpResponseForbidden("Login com Telegram ainda nao configurado.")
    next_url = request.GET.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        request.session["oidc_next"] = next_url
    redirect_uri = request.build_absolute_uri(reverse("telegram-callback"))
    return get_telegram_client().authorize_redirect(request, redirect_uri)


@require_GET
def telegram_callback(request: HttpRequest) -> HttpResponse:
    if not settings.TELEGRAM_OIDC_ENABLED:
        return HttpResponseForbidden("Login com Telegram ainda nao configurado.")
    try:
        token = get_telegram_client().authorize_access_token(request)
        raw_claims = token.get("userinfo")
        if not isinstance(raw_claims, Mapping):
            raise TelegramIdentityError("ID token sem claims OIDC validadas.")
        user = login_or_create_telegram_user(TelegramClaims.from_mapping(raw_claims))
    except (OAuthError, TelegramIdentityError):
        LOGGER.warning("Falha ao autenticar uma identidade Telegram")
        messages.error(request, "Nao foi possivel autenticar com o Telegram.")
        return redirect("landing")

    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    next_url = request.session.pop("oidc_next", reverse("dashboard"))
    return redirect(next_url)


@require_POST
def user_logout(request: HttpRequest) -> HttpResponse:
    logout(request)
    return redirect("landing")


@require_GET
def privacy_policy(request: HttpRequest) -> HttpResponse:
    return render(request, "tenancy/privacy_policy.html")


@login_required
def delete_account(request: HttpRequest) -> HttpResponse:
    if request.method == "GET":
        return render(request, "tenancy/delete_account.html")
    if request.method != "POST":
        return HttpResponse(status=405)
    if request.POST.get("confirmation", "").strip() != "EXCLUIR MINHA CONTA":
        messages.error(request, "Digite a frase de confirmacao exatamente como exibida.")
        return render(request, "tenancy/delete_account.html", status=400)

    user = request.user
    logout(request)
    try:
        webhook_removed = delete_personal_account(user)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("landing")
    if webhook_removed:
        messages.success(request, "Sua conta e seus dados pessoais foram excluidos.")
    else:
        messages.warning(
            request,
            "Seus dados locais foram excluidos. O webhook remoto nao respondeu, "
            "mas a credencial do bot foi apagada deste servidor.",
        )
    return redirect("landing")
