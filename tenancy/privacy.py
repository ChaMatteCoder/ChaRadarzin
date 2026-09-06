from __future__ import annotations

import logging

from django.core.exceptions import ImproperlyConfigured
from django.db import transaction

from telegram_bots.client import TelegramApiError, TelegramTransportError
from telegram_bots.crypto import BotTokenCipher, SecretDecryptionError
from telegram_bots.models import ManagedBot
from telegram_bots.client import TelegramApiClient
from tenancy.models import BetaAccessGrant, Tenant, User


LOGGER = logging.getLogger(__name__)


def disconnect_remote_bot_best_effort(bot: ManagedBot) -> bool:
    """Remove the remote webhook when possible; never retain a token after deletion."""
    if not bot.has_token:
        return True
    try:
        token = BotTokenCipher.from_settings().decrypt(
            bot.token_ciphertext,
            bot.token_key_version,
        )
        return TelegramApiClient(token).delete_webhook(drop_pending_updates=True)
    except (
        TelegramApiError,
        TelegramTransportError,
        SecretDecryptionError,
        ImproperlyConfigured,
        ValueError,
    ):
        LOGGER.warning(
            "account_deletion_remote_webhook_failed",
            extra={"event_code": "ACCOUNT_DELETE_WEBHOOK_FAILED", "status": "WARNING"},
        )
        return False


def delete_personal_account(user: User) -> bool:
    """Erase one single-user tenant and its local personal data."""
    if user.is_staff or user.tenant_id is None:
        raise ValueError("Conta administrativa nao pode ser excluida por este fluxo.")
    tenant = Tenant.objects.get(pk=user.tenant_id)
    if tenant.users.exclude(pk=user.pk).exists():
        raise ValueError("A conta pertence a um espaco compartilhado.")

    bot = ManagedBot.objects.filter(tenant=tenant).first()
    webhook_removed = True
    if bot is not None:
        webhook_removed = disconnect_remote_bot_best_effort(bot)

    telegram_user_id = getattr(getattr(user, "telegram_identity", None), "telegram_user_id", None)
    with transaction.atomic():
        if telegram_user_id is not None:
            BetaAccessGrant.objects.filter(telegram_user_id=telegram_user_id).delete()
        user.delete()
        tenant.delete()
    LOGGER.info(
        "personal_account_deleted",
        extra={"event_code": "PERSONAL_ACCOUNT_DELETED", "status": "SUCCESS"},
    )
    return webhook_removed
