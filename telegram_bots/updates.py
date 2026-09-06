from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from monitoring.models import MonitoredProduct, ProductStatus
from monitoring.job_queue import enqueue_collection_now
from telegram_bots.client import TelegramApiClient, TelegramApiError, TelegramTransportError
from telegram_bots.crypto import BotTokenCipher, SecretDecryptionError
from telegram_bots.models import (
    BotOnboardingSession,
    BotSecurityEvent,
    ManagedBot,
    ManagedBotStatus,
    ManualRefreshRequest,
    OnboardingStatus,
)


class TelegramUpdateError(RuntimeError):
    """An update could not be completed and Telegram should retry it."""


def update_kind(update: dict) -> str:
    for candidate in ("managed_bot", "message", "callback_query", "my_chat_member"):
        if candidate in update:
            return candidate
    return "unsupported"


def _client_for(bot: ManagedBot) -> TelegramApiClient:
    try:
        token = BotTokenCipher.from_settings().decrypt(
            bot.token_ciphertext,
            bot.token_key_version,
        )
    except SecretDecryptionError as exc:
        bot.status = ManagedBotStatus.ERROR
        bot.last_error_code = "TOKEN_DECRYPTION_FAILED"
        bot.save(update_fields=["status", "last_error_code", "updated_at"])
        raise TelegramUpdateError("TOKEN_DECRYPTION_FAILED") from exc
    return TelegramApiClient(token)


def _mark_api_failure(bot: ManagedBot, exc: Exception) -> None:
    if isinstance(exc, TelegramApiError) and exc.error_code == 401:
        bot.status = ManagedBotStatus.REVOKED
        bot.token_ciphertext = ""
        bot.token_key_version = None
        bot.webhook_secret_digest = ""
        bot.chat_id = None
        bot.last_error_code = "TELEGRAM_API_UNAUTHORIZED"
        bot.disconnected_at = timezone.now()
        bot.save(
            update_fields=[
                "status",
                "token_ciphertext",
                "token_key_version",
                "webhook_secret_digest",
                "chat_id",
                "last_error_code",
                "disconnected_at",
                "updated_at",
            ]
        )
        BotSecurityEvent.objects.create(
            tenant=bot.tenant,
            bot=bot,
            event_code="BOT_TOKEN_REVOKED",
        )
    else:
        bot.last_error_code = "TELEGRAM_DELIVERY_FAILED"
        bot.save(update_fields=["last_error_code", "updated_at"])


def _send(
    bot: ManagedBot,
    chat_id: int,
    text: str,
    *,
    client: TelegramApiClient | None = None,
    reply_markup: dict | None = None,
) -> None:
    client = client or _client_for(bot)
    try:
        client.send_message(chat_id, text, reply_markup=reply_markup)
    except (TelegramApiError, TelegramTransportError) as exc:
        _mark_api_failure(bot, exc)
        raise TelegramUpdateError("TELEGRAM_DELIVERY_FAILED") from exc


def _panel_url() -> str:
    return settings.PUBLIC_BASE_URL + reverse("bot-status")


def _welcome_text(bot: ManagedBot) -> str:
    return (
        "<b>Seu ChaRadarzin esta conectado.</b>\n\n"
        "Eu aviso aqui quando encontrar uma mudanca de preco relevante. "
        "Use /produtos para ver seu radar e /status para conferir a conexao."
    )


def _products_text(bot: ManagedBot) -> str:
    products = list(
        MonitoredProduct.objects.filter(tenant=bot.tenant)
        .order_by("name", "id")
        .values_list("name", "status")[:21]
    )
    if not products:
        return "Seu radar ainda nao tem produtos. Cadastre o primeiro pelo painel."
    visible = products[:20]
    lines = ["<b>Produtos no seu radar</b>"]
    for name, status in visible:
        marker = "Ativo" if status == ProductStatus.ACTIVE else "Pausado"
        lines.append(f"• {escape(name)} — {marker}")
    if len(products) > 20:
        lines.append("• … e outros produtos")
    return "\n".join(lines)


def _status_text(bot: ManagedBot) -> str:
    active = MonitoredProduct.objects.filter(
        tenant=bot.tenant,
        status=ProductStatus.ACTIVE,
    ).count()
    paused = MonitoredProduct.objects.filter(
        tenant=bot.tenant,
        status=ProductStatus.PAUSED,
    ).count()
    return (
        "<b>Estado do seu radar</b>\n"
        f"Conexao: {escape(bot.get_status_display())}\n"
        f"Produtos ativos: {active}\n"
        f"Produtos pausados: {paused}"
    )


def _pause_products(bot: ManagedBot) -> int:
    return MonitoredProduct.objects.filter(
        tenant=bot.tenant,
        status=ProductStatus.ACTIVE,
    ).update(status=ProductStatus.PAUSED, updated_at=timezone.now())


def _request_refresh(bot: ManagedBot) -> bool:
    now = timezone.now()
    cutoff = now - timedelta(
        minutes=settings.TELEGRAM_MANUAL_REFRESH_COOLDOWN_MINUTES
    )
    if ManualRefreshRequest.objects.filter(
        tenant=bot.tenant,
        requested_at__gte=cutoff,
    ).exists():
        return False
    with transaction.atomic():
        job = enqueue_collection_now(now=now)
        ManualRefreshRequest.objects.create(
            tenant=bot.tenant,
            bot=bot,
            collection_job=job,
            requested_by_telegram_user_id=bot.owner_telegram_user_id,
        )
    return True


def _reject_non_owner(bot: ManagedBot, context: str) -> str:
    BotSecurityEvent.objects.create(
        tenant=bot.tenant,
        bot=bot,
        event_code="NON_OWNER_UPDATE_REJECTED",
        context=context[:160],
    )
    return "NON_OWNER_REJECTED"


def _handle_message(
    bot: ManagedBot,
    message: dict,
    *,
    client: TelegramApiClient | None,
) -> str:
    sender = message.get("from")
    chat = message.get("chat")
    if not isinstance(sender, dict) or not isinstance(chat, dict):
        return "INVALID_MESSAGE"
    sender_id = sender.get("id")
    chat_id = chat.get("id")
    if (
        sender_id != bot.owner_telegram_user_id
        or chat_id != bot.owner_telegram_user_id
        or chat.get("type") != "private"
    ):
        return _reject_non_owner(bot, f"message:{sender_id or 'unknown'}")

    text = str(message.get("text") or "").strip()
    command = text.split(maxsplit=1)[0].split("@", 1)[0].casefold()
    if command == "/start":
        now = timezone.now()
        bot.chat_id = int(chat_id)
        bot.status = ManagedBotStatus.ACTIVE
        bot.activated_at = bot.activated_at or now
        bot.last_seen_at = now
        bot.last_error_code = ""
        bot.save(
            update_fields=[
                "chat_id",
                "status",
                "activated_at",
                "last_seen_at",
                "last_error_code",
                "updated_at",
            ]
        )
        BotOnboardingSession.objects.filter(bot=bot, is_open=True).update(
            status=OnboardingStatus.ACTIVE,
            is_open=False,
            updated_at=now,
        )
        _send(bot, int(chat_id), _welcome_text(bot), client=client)
        return "ACTIVATED"

    bot.last_seen_at = timezone.now()
    bot.save(update_fields=["last_seen_at", "updated_at"])
    panel_button = {
        "inline_keyboard": [[{"text": "Abrir painel", "url": _panel_url()}]]
    }
    if command == "/produtos":
        _send(bot, int(chat_id), _products_text(bot), client=client, reply_markup=panel_button)
        return "PRODUCTS_SENT"
    if command == "/status":
        _send(bot, int(chat_id), _status_text(bot), client=client)
        return "STATUS_SENT"
    if command == "/pausar":
        paused = _pause_products(bot)
        _send(bot, int(chat_id), f"Alertas pausados para {paused} produto(s).", client=client)
        return "PRODUCTS_PAUSED"
    if command == "/configurar":
        _send(bot, int(chat_id), "Abra seu painel privado para configurar o radar.", client=client, reply_markup=panel_button)
        return "PANEL_SENT"
    if command == "/atualizar":
        created = _request_refresh(bot)
        message_text = (
            "Atualizacao solicitada. Avisarei quando o processamento terminar."
            if created
            else "Ja existe uma solicitacao recente. Aguarde antes de pedir outra."
        )
        _send(bot, int(chat_id), message_text, client=client)
        return "REFRESH_REQUESTED" if created else "REFRESH_COOLDOWN"

    _send(
        bot,
        int(chat_id),
        "Comandos: /produtos, /status, /pausar, /configurar e /atualizar.",
        client=client,
    )
    return "HELP_SENT"


def _handle_membership(bot: ManagedBot, membership: dict) -> str:
    chat = membership.get("chat")
    member = membership.get("new_chat_member")
    if not isinstance(chat, dict) or not isinstance(member, dict):
        return "INVALID_MEMBERSHIP"
    if chat.get("type") != "private" or chat.get("id") != bot.owner_telegram_user_id:
        return _reject_non_owner(bot, f"membership:{chat.get('id', 'unknown')}")
    status = member.get("status")
    bot.last_seen_at = timezone.now()
    if status in {"kicked", "left"}:
        bot.status = ManagedBotStatus.BLOCKED
        bot.last_error_code = "BOT_BLOCKED_BY_OWNER"
        outcome = "BOT_BLOCKED"
    elif status == "member":
        bot.status = ManagedBotStatus.ACTIVE if bot.chat_id else ManagedBotStatus.AWAITING_START
        bot.last_error_code = ""
        outcome = "BOT_UNBLOCKED"
    else:
        outcome = "MEMBERSHIP_SEEN"
    bot.save(update_fields=["status", "last_seen_at", "last_error_code", "updated_at"])
    return outcome


def _handle_callback(
    bot: ManagedBot,
    callback: dict,
    *,
    client: TelegramApiClient | None,
) -> str:
    sender = callback.get("from")
    callback_id = callback.get("id")
    callback_message = callback.get("message")
    callback_chat = callback_message.get("chat") if isinstance(callback_message, dict) else None
    if (
        not isinstance(sender, dict)
        or sender.get("id") != bot.owner_telegram_user_id
        or not isinstance(callback_chat, dict)
        or callback_chat.get("id") != bot.owner_telegram_user_id
        or callback_chat.get("type") != "private"
    ):
        return _reject_non_owner(bot, f"callback:{sender.get('id') if isinstance(sender, dict) else 'unknown'}")
    if not isinstance(callback_id, str):
        return "INVALID_CALLBACK"
    data = callback.get("data")
    if isinstance(data, str) and data.startswith("pause_product:"):
        raw_product_id = data.removeprefix("pause_product:")
        try:
            product_id = UUID(raw_product_id)
        except (ValueError, TypeError):
            product_id = None
        if product_id is None:
            answer = "Produto inválido."
            outcome = "INVALID_CALLBACK"
        else:
            changed = MonitoredProduct.objects.filter(
                pk=product_id,
                tenant_id=bot.tenant_id,
                status=ProductStatus.ACTIVE,
            ).update(status=ProductStatus.PAUSED, updated_at=timezone.now())
            answer = (
                "Alertas pausados para este produto."
                if changed
                else "Este produto já está pausado ou não existe."
            )
            outcome = "PRODUCT_PAUSED" if changed else "PRODUCT_ALREADY_PAUSED"
    else:
        answer = "Use os comandos ou abra o painel."
        outcome = "CALLBACK_ANSWERED"
    client = client or _client_for(bot)
    try:
        client.answer_callback_query(callback_id, answer)
    except (TelegramApiError, TelegramTransportError) as exc:
        _mark_api_failure(bot, exc)
        raise TelegramUpdateError("CALLBACK_DELIVERY_FAILED") from exc
    return outcome


def process_personal_bot_update(
    bot: ManagedBot,
    update: dict,
    *,
    client: TelegramApiClient | None = None,
) -> str:
    if "message" in update and isinstance(update["message"], dict):
        return _handle_message(bot, update["message"], client=client)
    if "my_chat_member" in update and isinstance(update["my_chat_member"], dict):
        return _handle_membership(bot, update["my_chat_member"])
    if "callback_query" in update and isinstance(update["callback_query"], dict):
        return _handle_callback(bot, update["callback_query"], client=client)
    return "UNSUPPORTED_UPDATE"
