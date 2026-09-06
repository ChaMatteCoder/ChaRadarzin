from __future__ import annotations

import secrets
from dataclasses import dataclass
from urllib.parse import urlparse

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from telegram_bots.client import (
    ManagerBotClient,
    TelegramApiClient,
    TelegramApiError,
    TelegramTransportError,
)
from telegram_bots.crypto import BotTokenCipher, SecretDecryptionError, webhook_secret_digest
from telegram_bots.models import (
    BotCredentialSource,
    BotOnboardingSession,
    BotSecurityEvent,
    ManagedBot,
    ManagedBotStatus,
    OnboardingStatus,
)
from tenancy.models import TelegramIdentity, Tenant


BOT_COMMANDS = [
    {"command": "start", "description": "Ativar seu radar pessoal"},
    {"command": "produtos", "description": "Listar produtos monitorados"},
    {"command": "status", "description": "Ver o estado do radar"},
    {"command": "pausar", "description": "Pausar seus alertas"},
    {"command": "configurar", "description": "Abrir o painel"},
    {"command": "atualizar", "description": "Solicitar uma atualizacao"},
]
PERSONAL_ALLOWED_UPDATES = ["message", "callback_query", "my_chat_member"]
MANAGER_ALLOWED_UPDATES = ["managed_bot"]


class ManagedBotConflict(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ManagerUpdateResult:
    outcome_code: str
    bot: ManagedBot | None = None


def manager_client_from_settings() -> ManagerBotClient:
    if not settings.TELEGRAM_MANAGER_ENABLED:
        raise ManagedBotConflict("MANAGER_DISABLED")
    return ManagerBotClient(settings.TELEGRAM_MANAGER_BOT_TOKEN)


def _public_webhook_url(bot: ManagedBot) -> str:
    parsed = urlparse(settings.PUBLIC_BASE_URL)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ManagedBotConflict("PUBLIC_BASE_URL_INVALID")
    return settings.PUBLIC_BASE_URL + reverse(
        "personal-bot-webhook",
        kwargs={"webhook_id": bot.webhook_public_id},
    )


def _safe_api_error_code(exc: Exception) -> str:
    if isinstance(exc, TelegramApiError):
        return f"TELEGRAM_API_{exc.method}_{exc.error_code or 'UNKNOWN'}"[:80]
    if isinstance(exc, TelegramTransportError):
        return f"TELEGRAM_TRANSPORT_{exc.method}"[:80]
    if isinstance(exc, SecretDecryptionError):
        return "TOKEN_DECRYPTION_FAILED"
    if isinstance(exc, ManagedBotConflict):
        return exc.code[:80]
    return "BOT_CONFIGURATION_FAILED"


def configure_personal_bot(
    bot: ManagedBot,
    token: str,
    *,
    manager_client: ManagerBotClient | None,
    child_client: TelegramApiClient | None = None,
    cipher: BotTokenCipher | None = None,
) -> ManagedBot:
    cipher = cipher or BotTokenCipher.from_settings()
    encrypted = cipher.encrypt(token)
    webhook_secret = secrets.token_urlsafe(32)
    bot.token_ciphertext = encrypted.ciphertext
    bot.token_key_version = encrypted.key_version
    bot.webhook_secret_digest = webhook_secret_digest(webhook_secret)
    bot.status = ManagedBotStatus.CONFIGURING
    bot.last_error_code = ""
    bot.save(
        update_fields=[
            "token_ciphertext",
            "token_key_version",
            "webhook_secret_digest",
            "status",
            "last_error_code",
            "updated_at",
        ]
    )

    client = child_client or TelegramApiClient(token)
    try:
        access_restricted = False
        if bot.credential_source == BotCredentialSource.MANAGED:
            if manager_client is None:
                raise ManagedBotConflict("MANAGER_CLIENT_REQUIRED")
            manager_client.restrict_managed_bot_to_owner(bot.telegram_bot_id)
            access_restricted = True

        bot_data = client.get_me()
        if int(bot_data.get("id", 0)) != bot.telegram_bot_id:
            raise ManagedBotConflict("BOT_TOKEN_ID_MISMATCH")
        if not bool(bot_data.get("is_bot")):
            raise ManagedBotConflict("TOKEN_DOES_NOT_BELONG_TO_BOT")

        client.set_my_commands(BOT_COMMANDS)
        client.set_webhook(
            _public_webhook_url(bot),
            webhook_secret,
            allowed_updates=PERSONAL_ALLOWED_UPDATES,
            drop_pending_updates=False,
        )
        bot.username = str(bot_data.get("username") or bot.username).lstrip("@")[:64]
        bot.display_name = str(
            bot_data.get("first_name") or bot.display_name or bot.username
        )[:160]
        bot.access_restricted = access_restricted
        bot.status = ManagedBotStatus.AWAITING_START
        bot.token_rotated_at = timezone.now()
        bot.disconnected_at = None
        bot.last_error_code = ""
        bot.save(
            update_fields=[
                "username",
                "display_name",
                "access_restricted",
                "status",
                "token_rotated_at",
                "disconnected_at",
                "last_error_code",
                "updated_at",
            ]
        )
        return bot
    except Exception as exc:
        bot.status = ManagedBotStatus.ERROR
        bot.last_error_code = _safe_api_error_code(exc)
        bot.save(update_fields=["status", "last_error_code", "updated_at"])
        raise


def _parse_managed_update(update: dict) -> tuple[int, dict, int, dict]:
    update_id = update.get("update_id")
    managed = update.get("managed_bot")
    if not isinstance(update_id, int) or not isinstance(managed, dict):
        raise ManagedBotConflict("INVALID_MANAGED_BOT_UPDATE")
    user = managed.get("user")
    bot_data = managed.get("bot")
    if not isinstance(user, dict) or not isinstance(bot_data, dict):
        raise ManagedBotConflict("INVALID_MANAGED_BOT_UPDATE")
    user_id = user.get("id")
    bot_id = bot_data.get("id")
    if not isinstance(user_id, int) or not isinstance(bot_id, int):
        raise ManagedBotConflict("INVALID_MANAGED_BOT_IDENTIFIERS")
    return user_id, user, bot_id, bot_data


def process_managed_bot_update(
    update: dict,
    *,
    manager_client: ManagerBotClient | None = None,
    child_client: TelegramApiClient | None = None,
    cipher: BotTokenCipher | None = None,
) -> ManagerUpdateResult:
    user_id, _user_data, bot_id, bot_data = _parse_managed_update(update)
    existing_by_bot = ManagedBot.objects.filter(telegram_bot_id=bot_id).first()
    if existing_by_bot is not None and existing_by_bot.owner_telegram_user_id != user_id:
        existing_by_bot.status = ManagedBotStatus.OWNER_CHANGED
        existing_by_bot.token_ciphertext = ""
        existing_by_bot.token_key_version = None
        existing_by_bot.webhook_secret_digest = ""
        existing_by_bot.chat_id = None
        existing_by_bot.disconnected_at = timezone.now()
        existing_by_bot.last_error_code = "OWNER_CHANGED"
        existing_by_bot.save(
            update_fields=[
                "status",
                "token_ciphertext",
                "token_key_version",
                "webhook_secret_digest",
                "chat_id",
                "disconnected_at",
                "last_error_code",
                "updated_at",
            ]
        )
        BotSecurityEvent.objects.create(
            tenant=existing_by_bot.tenant,
            bot=existing_by_bot,
            event_code="MANAGED_BOT_OWNER_CHANGED",
        )
        return ManagerUpdateResult("OWNER_CHANGED", existing_by_bot)

    identity = TelegramIdentity.objects.select_related("user__tenant").filter(
        telegram_user_id=user_id
    ).first()
    if identity is None or identity.user.tenant_id is None:
        BotSecurityEvent.objects.create(event_code="UNASSOCIATED_MANAGED_BOT_UPDATE")
        return ManagerUpdateResult("UNASSOCIATED_OWNER")

    tenant = identity.user.tenant
    tenant_bot = ManagedBot.objects.filter(tenant=tenant).first()
    if tenant_bot is not None and tenant_bot.telegram_bot_id != bot_id:
        BotSecurityEvent.objects.create(
            tenant=tenant,
            bot=tenant_bot,
            event_code="SECOND_MANAGED_BOT_REJECTED",
        )
        return ManagerUpdateResult("BOT_ALREADY_ASSIGNED", tenant_bot)

    onboarding = BotOnboardingSession.objects.filter(
        tenant=tenant,
        telegram_user_id=user_id,
        is_open=True,
    ).first()
    if existing_by_bot is None and onboarding is None:
        BotSecurityEvent.objects.create(
            tenant=tenant,
            event_code="MANAGED_BOT_WITHOUT_ONBOARDING",
        )
        return ManagerUpdateResult("NO_OPEN_ONBOARDING")

    if onboarding is not None and onboarding.expires_at <= timezone.now():
        onboarding.status = OnboardingStatus.EXPIRED
        onboarding.is_open = False
        onboarding.save(update_fields=["status", "is_open", "updated_at"])
        return ManagerUpdateResult("ONBOARDING_EXPIRED")

    manager_client = manager_client or manager_client_from_settings()
    token = manager_client.get_managed_bot_token(bot_id)
    username = str(bot_data.get("username") or "").lstrip("@")
    if not username:
        raise ManagedBotConflict("BOT_USERNAME_REQUIRED")
    display_name = str(bot_data.get("first_name") or username or f"Bot {bot_id}")

    with transaction.atomic():
        bot, _created = ManagedBot.objects.update_or_create(
            tenant=tenant,
            defaults={
                "telegram_bot_id": bot_id,
                "owner_telegram_user_id": user_id,
                "username": username[:64],
                "display_name": display_name[:160],
                "credential_source": BotCredentialSource.MANAGED,
                "status": ManagedBotStatus.CONFIGURING,
            },
        )
        if onboarding is not None:
            onboarding.bot = bot
            onboarding.status = OnboardingStatus.CONFIGURING
            onboarding.error_code = ""
            onboarding.save(
                update_fields=["bot", "status", "error_code", "updated_at"]
            )

    try:
        configure_personal_bot(
            bot,
            token,
            manager_client=manager_client,
            child_client=child_client,
            cipher=cipher,
        )
    except Exception as exc:
        if onboarding is not None:
            onboarding.status = OnboardingStatus.CONFIGURING
            onboarding.error_code = _safe_api_error_code(exc)
            onboarding.save(update_fields=["status", "error_code", "updated_at"])
        raise

    if onboarding is not None:
        onboarding.bot = bot
        onboarding.status = OnboardingStatus.AWAITING_START
        onboarding.save(update_fields=["bot", "status", "updated_at"])
    return ManagerUpdateResult("AWAITING_START", bot)


def attach_manual_fallback_bot(
    tenant: Tenant,
    token: str,
    *,
    child_client: TelegramApiClient | None = None,
    cipher: BotTokenCipher | None = None,
) -> ManagedBot:
    if ManagedBot.objects.filter(tenant=tenant).exists():
        raise ManagedBotConflict("BOT_ALREADY_EXISTS")
    identity = TelegramIdentity.objects.filter(user__tenant=tenant).first()
    if identity is None:
        raise ManagedBotConflict("TELEGRAM_IDENTITY_REQUIRED")
    client = child_client or TelegramApiClient(token)
    bot_data = client.get_me()
    if not bool(bot_data.get("is_bot")):
        raise ManagedBotConflict("TOKEN_DOES_NOT_BELONG_TO_BOT")
    bot = ManagedBot.objects.create(
        tenant=tenant,
        telegram_bot_id=int(bot_data["id"]),
        owner_telegram_user_id=identity.telegram_user_id,
        username=str(bot_data.get("username") or "fallback_bot").lstrip("@")[:64],
        display_name=str(bot_data.get("first_name") or "Bot pessoal")[:160],
        credential_source=BotCredentialSource.MANUAL_FALLBACK,
    )
    return configure_personal_bot(
        bot,
        token,
        manager_client=None,
        child_client=client,
        cipher=cipher,
    )


def rotate_managed_bot_token(
    bot: ManagedBot,
    *,
    manager_client: ManagerBotClient | None = None,
    child_client: TelegramApiClient | None = None,
    cipher: BotTokenCipher | None = None,
) -> ManagedBot:
    if bot.credential_source != BotCredentialSource.MANAGED:
        raise ManagedBotConflict("ROTATION_REQUIRES_MANAGED_BOT")
    manager_client = manager_client or manager_client_from_settings()
    token = manager_client.replace_managed_bot_token(bot.telegram_bot_id)
    return configure_personal_bot(
        bot,
        token,
        manager_client=manager_client,
        child_client=child_client,
        cipher=cipher,
    )


def disconnect_bot(
    bot: ManagedBot,
    *,
    child_client: TelegramApiClient | None = None,
    cipher: BotTokenCipher | None = None,
) -> None:
    delete_error = ""
    if bot.has_token:
        cipher = cipher or BotTokenCipher.from_settings()
        try:
            token = cipher.decrypt(bot.token_ciphertext, bot.token_key_version)
            client = child_client or TelegramApiClient(token)
            client.delete_webhook(drop_pending_updates=False)
        except SecretDecryptionError:
            delete_error = "TOKEN_DECRYPTION_FAILED"
        except (TelegramApiError, TelegramTransportError) as exc:
            delete_error = _safe_api_error_code(exc)
    bot.status = ManagedBotStatus.DISCONNECTED
    bot.token_ciphertext = ""
    bot.token_key_version = None
    bot.webhook_secret_digest = ""
    bot.chat_id = None
    bot.disconnected_at = timezone.now()
    bot.last_error_code = delete_error
    bot.save(
        update_fields=[
            "status",
            "token_ciphertext",
            "token_key_version",
            "webhook_secret_digest",
            "chat_id",
            "disconnected_at",
            "last_error_code",
            "updated_at",
        ]
    )


def mark_bot_revoked(bot: ManagedBot, *, error_code: str = "TELEGRAM_API_UNAUTHORIZED") -> None:
    """Remove unusable credentials after Telegram has revoked a bot token."""
    bot.status = ManagedBotStatus.REVOKED
    bot.token_ciphertext = ""
    bot.token_key_version = None
    bot.webhook_secret_digest = ""
    bot.chat_id = None
    bot.disconnected_at = timezone.now()
    bot.last_error_code = error_code[:80]
    bot.save(
        update_fields=[
            "status",
            "token_ciphertext",
            "token_key_version",
            "webhook_secret_digest",
            "chat_id",
            "disconnected_at",
            "last_error_code",
            "updated_at",
        ]
    )


def configure_manager_webhook(
    client: ManagerBotClient | None = None,
) -> dict:
    client = client or manager_client_from_settings()
    me = client.get_me()
    if not bool(me.get("can_manage_bots")):
        raise ManagedBotConflict("MANAGER_CAPABILITY_MISSING")
    configured_username = settings.TELEGRAM_MANAGER_BOT_USERNAME.casefold()
    actual_username = str(me.get("username") or "").lstrip("@").casefold()
    if configured_username and actual_username != configured_username:
        raise ManagedBotConflict("MANAGER_USERNAME_MISMATCH")
    manager_url = settings.PUBLIC_BASE_URL + reverse(
        "manager-bot-webhook",
        kwargs={"endpoint_id": settings.TELEGRAM_MANAGER_WEBHOOK_ID},
    )
    client.set_webhook(
        manager_url,
        settings.TELEGRAM_MANAGER_WEBHOOK_SECRET,
        allowed_updates=MANAGER_ALLOWED_UPDATES,
        drop_pending_updates=False,
    )
    return me
