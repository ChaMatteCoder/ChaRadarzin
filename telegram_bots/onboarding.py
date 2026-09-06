from __future__ import annotations

import re
from datetime import timedelta
from urllib.parse import quote, urlencode

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from telegram_bots.models import (
    BotOnboardingSession,
    ManagedBot,
    OnboardingStatus,
)
from tenancy.models import TelegramIdentity, User


BOT_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{5,32}$")


class OnboardingError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def validate_bot_username(value: str) -> str:
    username = value.strip().lstrip("@")
    if not BOT_USERNAME_PATTERN.fullmatch(username) or not username.casefold().endswith(
        "bot"
    ):
        raise OnboardingError(
            "INVALID_USERNAME",
            "O username deve ter 5 a 32 caracteres, usar letras, numeros ou _ e terminar em bot.",
        )
    return username


def validate_bot_name(value: str) -> str:
    name = " ".join(value.split())
    if not 1 <= len(name) <= 64:
        raise OnboardingError(
            "INVALID_NAME",
            "O nome do bot deve ter entre 1 e 64 caracteres.",
        )
    return name


def _identity_for(user: User):
    try:
        return user.telegram_identity
    except TelegramIdentity.DoesNotExist as exc:
        raise OnboardingError(
            "TELEGRAM_IDENTITY_REQUIRED",
            "A conta precisa estar autenticada com Telegram.",
        ) from exc


@transaction.atomic
def start_onboarding(
    user: User,
    *,
    suggested_name: str = "",
    suggested_username: str = "",
) -> BotOnboardingSession:
    if user.tenant_id is None:
        raise OnboardingError("TENANT_REQUIRED", "Conta sem tenant.")
    identity = _identity_for(user)
    if ManagedBot.objects.filter(tenant_id=user.tenant_id).exists():
        raise OnboardingError(
            "BOT_ALREADY_EXISTS",
            "Esta conta ja possui um bot pessoal.",
        )

    now = timezone.now()
    open_session = (
        BotOnboardingSession.objects.select_for_update()
        .filter(tenant_id=user.tenant_id, is_open=True)
        .first()
    )
    if open_session is not None:
        if open_session.expires_at > now:
            return open_session
        open_session.status = OnboardingStatus.EXPIRED
        open_session.is_open = False
        open_session.save(update_fields=["status", "is_open", "updated_at"])

    default_name = f"ChaRadarzin de {identity.display_name.split()[0]}"
    suffix = str(identity.telegram_user_id)[-8:]
    default_username = f"ChaRadar{suffix}Bot"
    return BotOnboardingSession.objects.create(
        tenant_id=user.tenant_id,
        telegram_user_id=identity.telegram_user_id,
        suggested_name=validate_bot_name(suggested_name or default_name),
        suggested_username=validate_bot_username(
            suggested_username or default_username
        ),
        expires_at=now
        + timedelta(minutes=settings.TELEGRAM_ONBOARDING_TTL_MINUTES),
    )


def build_managed_bot_deep_link(session: BotOnboardingSession) -> str:
    manager_username = settings.TELEGRAM_MANAGER_BOT_USERNAME.strip().lstrip("@")
    if not manager_username:
        raise OnboardingError(
            "MANAGER_NOT_CONFIGURED",
            "O bot gerenciador ainda nao esta configurado.",
        )
    return (
        "https://t.me/newbot/"
        f"{quote(manager_username, safe='')}/"
        f"{quote(session.suggested_username, safe='')}?"
        + urlencode({"name": session.suggested_name})
    )


def expire_stale_onboarding() -> int:
    return BotOnboardingSession.objects.filter(
        is_open=True,
        expires_at__lte=timezone.now(),
    ).update(
        is_open=False,
        status=OnboardingStatus.EXPIRED,
        updated_at=timezone.now(),
    )
