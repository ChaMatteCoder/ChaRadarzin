from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Callable

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from monitoring.models import (
    AlertEvent,
    NotificationDelivery,
    NotificationDeliveryStatus,
)
from monitoring.url_safety import ExactProductUrlError, validate_exact_product_url
from telegram_bots.client import TelegramApiClient, TelegramApiError, TelegramTransportError
from telegram_bots.configuration import mark_bot_revoked
from telegram_bots.crypto import BotTokenCipher, SecretDecryptionError
from telegram_bots.models import BotSecurityEvent, ManagedBot, ManagedBotStatus


PERSONAL_CHANNEL = "TELEGRAM_PERSONAL"
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    status: str
    delivery_id: int
    message_id: int | None = None
    error_code: str = ""


@dataclass(frozen=True, slots=True)
class TestNotificationResult:
    status: str
    message_id: int | None = None
    error_code: str = ""


def _money(cents: int | None) -> str:
    if cents is None:
        return "não informado"
    value = f"{Decimal(cents) / Decimal(100):,.2f}"
    return f"R$ {value.replace(',', '_').replace('.', ',').replace('_', '.')}"


def _panel_url(name: str, **kwargs) -> str:
    return settings.PUBLIC_BASE_URL + reverse(name, kwargs=kwargs)


def _safe_offer_url(event: AlertEvent) -> str:
    source = event.observation.source
    try:
        return validate_exact_product_url(source.url).canonical_url
    except ExactProductUrlError:
        return ""


def _alert_text(event: AlertEvent) -> str:
    observation = event.observation
    product = event.product
    lines = [
        f"<b>{escape(product.name)}</b>",
        f"{escape(event.reason)}",
        "",
        f"Preço atual: <b>{_money(event.current_price_cents)}</b>",
        f"Preço anterior: {_money(event.previous_price_cents)}",
        f"Variação: {escape(str(event.variation_percent or 'não informada'))}%",
        f"Loja: {escape(observation.source.store)}",
    ]
    if observation.seller:
        lines.append(f"Vendedor: {escape(observation.seller)}")
    lines.append(f"Frete: {_money(observation.shipping_price_cents)}")
    if observation.delivery_min_days is not None or observation.delivery_max_days is not None:
        minimum = observation.delivery_min_days or observation.delivery_max_days
        maximum = observation.delivery_max_days or observation.delivery_min_days
        prazo = str(minimum) if minimum == maximum else f"{minimum}–{maximum}"
        lines.append(f"Entrega: {escape(prazo)} dias")
    lines.append(f"Custo total: <b>{_money(observation.total_price_cents)}</b>")
    if event.historical_low_cents is not None:
        lines.append(f"Menor histórico: {_money(event.historical_low_cents)}")
    if event.target_price_cents is not None:
        lines.append(f"Preço-alvo: {_money(event.target_price_cents)}")
    return "\n".join(lines)


def _reply_markup(event: AlertEvent) -> dict:
    buttons: list[list[dict[str, str]]] = []
    offer_url = _safe_offer_url(event)
    if offer_url:
        buttons.append([{"text": "Ver oferta", "url": offer_url}])
    buttons.append(
        [
            {
                "text": "Ver histórico",
                "url": _panel_url("product-detail-page", product_id=event.product_id),
            },
            {
                "text": "Abrir painel",
                "url": _panel_url("dashboard"),
            },
        ]
    )
    buttons.append(
        [
            {
                "text": "Pausar alertas",
                "callback_data": f"pause_product:{event.product_id}",
            }
        ]
    )
    return {"inline_keyboard": buttons}


def _reserve_delivery(
    event: AlertEvent,
    *,
    now: datetime,
) -> tuple[NotificationDelivery, ManagedBot | None, str]:
    with transaction.atomic():
        delivery, _created = NotificationDelivery.objects.select_for_update().get_or_create(
            alert_event=event,
            channel=PERSONAL_CHANNEL,
            defaults={
                "tenant_id": event.tenant_id,
                "run_id": event.observation.run_id,
                "product_id": event.product_id,
                "delivery_key": f"{PERSONAL_CHANNEL}:{event.pk}",
                "attempted_at": now,
                "event_type": event.event_type,
                "price_cents": event.current_price_cents,
                "previous_price_cents": event.previous_price_cents,
                "status": NotificationDeliveryStatus.PENDING,
            },
        )
        if delivery.status == NotificationDeliveryStatus.SENT:
            return delivery, None, "SENT"
        if (
            delivery.status == NotificationDeliveryStatus.SENDING
            and delivery.attempted_at
            and delivery.attempted_at
            >= now - timedelta(seconds=settings.TELEGRAM_DELIVERY_LEASE_SECONDS)
        ):
            return delivery, None, "BUSY"

        bot = ManagedBot.objects.filter(tenant_id=event.tenant_id).first()
        if (
            bot is None
            or bot.status != ManagedBotStatus.ACTIVE
            or bot.chat_id is None
            or not bot.has_token
        ):
            delivery.status = NotificationDeliveryStatus.BLOCKED
            delivery.attempted_at = now
            delivery.attempt_count += 1
            blocked = bot is not None and bot.status == ManagedBotStatus.BLOCKED
            delivery.error_code = "BOT_BLOCKED" if blocked else "BOT_NOT_CONNECTED"
            delivery.error_message = (
                "O bot pessoal foi bloqueado pelo proprietario."
                if blocked
                else "O bot pessoal ainda não está conectado."
            )
            delivery.save(
                update_fields=[
                    "status",
                    "attempted_at",
                    "attempt_count",
                    "error_code",
                    "error_message",
                ]
            )
            return delivery, bot, "BLOCKED"

        delivery.status = NotificationDeliveryStatus.SENDING
        delivery.attempted_at = now
        delivery.attempt_count += 1
        delivery.error_code = ""
        delivery.error_message = ""
        delivery.save(
            update_fields=[
                "status",
                "attempted_at",
                "attempt_count",
                "error_code",
                "error_message",
            ]
        )
        return delivery, bot, "SEND"


def _client_for(bot: ManagedBot) -> TelegramApiClient:
    try:
        token = BotTokenCipher.from_settings().decrypt(
            bot.token_ciphertext,
            bot.token_key_version,
        )
        client = TelegramApiClient(token)
    except (SecretDecryptionError, ImproperlyConfigured) as exc:
        bot.status = ManagedBotStatus.ERROR
        bot.last_error_code = "TOKEN_DECRYPTION_FAILED"
        bot.save(update_fields=["status", "last_error_code", "updated_at"])
        raise RuntimeError("TOKEN_DECRYPTION_FAILED") from exc
    except ValueError as exc:
        bot.status = ManagedBotStatus.ERROR
        bot.last_error_code = "TOKEN_INVALID"
        bot.save(update_fields=["status", "last_error_code", "updated_at"])
        raise RuntimeError("TOKEN_INVALID") from exc
    return client


def _failure_code(exc: Exception) -> str:
    if isinstance(exc, TelegramApiError):
        return f"TELEGRAM_API_{exc.method}_{exc.error_code or 'UNKNOWN'}"[:80]
    if isinstance(exc, TelegramTransportError):
        return f"TELEGRAM_TRANSPORT_{exc.method}"[:80]
    if isinstance(exc, SecretDecryptionError):
        return "TOKEN_DECRYPTION_FAILED"
    if isinstance(exc, RuntimeError) and str(exc) == "TOKEN_DECRYPTION_FAILED":
        return "TOKEN_DECRYPTION_FAILED"
    if isinstance(exc, RuntimeError) and str(exc) == "TOKEN_INVALID":
        return "TOKEN_INVALID"
    return "TELEGRAM_DELIVERY_FAILED"


def _record_api_failure(bot: ManagedBot, exc: Exception) -> str:
    code = _failure_code(exc)
    if isinstance(exc, TelegramApiError) and exc.error_code == 401:
        mark_bot_revoked(bot)
        BotSecurityEvent.objects.create(
            tenant=bot.tenant,
            bot=bot,
            event_code="BOT_TOKEN_REVOKED",
        )
        return "BOT_REVOKED"
    if isinstance(exc, TelegramApiError) and exc.error_code == 403:
        bot.status = ManagedBotStatus.BLOCKED
        bot.last_error_code = "TELEGRAM_API_FORBIDDEN"
        bot.disconnected_at = timezone.now()
        bot.save(
            update_fields=["status", "last_error_code", "disconnected_at", "updated_at"]
        )
        BotSecurityEvent.objects.create(
            tenant=bot.tenant,
            bot=bot,
            event_code="BOT_BLOCKED_BY_TELEGRAM",
        )
        return "BOT_BLOCKED"
    bot.last_error_code = code
    bot.save(update_fields=["last_error_code", "updated_at"])
    return code


def send_test_notification(
    bot: ManagedBot,
    *,
    client: TelegramApiClient | None = None,
    now: datetime | None = None,
) -> TestNotificationResult:
    """Send an operational test without creating price or alert history."""
    if bot.status != ManagedBotStatus.ACTIVE or bot.chat_id is None or not bot.has_token:
        return TestNotificationResult(status="BLOCKED", error_code="BOT_NOT_CONNECTED")

    markup = {
        "inline_keyboard": [
            [{"text": "Abrir painel", "url": _panel_url("dashboard")}]
        ]
    }
    text = (
        "<b>Teste do ChaRadarzin</b>\n\n"
        "Esta é uma notificação operacional de teste. "
        "Nenhum preço, alerta ou histórico foi alterado."
    )
    try:
        telegram_client = client or _client_for(bot)
        message = telegram_client.send_message(bot.chat_id, text, reply_markup=markup)
    except (TelegramApiError, TelegramTransportError, RuntimeError) as exc:
        error_code = _record_api_failure(bot, exc)
        LOGGER.warning(
            "telegram_test_notification_failed",
            extra={"event_code": error_code, "status": "FAILED"},
        )
        return TestNotificationResult(status="FAILED", error_code=error_code)

    current = now or timezone.now()
    message_id = message.get("message_id") if isinstance(message, dict) else None
    bot.last_seen_at = current
    bot.last_error_code = ""
    bot.save(update_fields=["last_seen_at", "last_error_code", "updated_at"])
    BotSecurityEvent.objects.create(
        tenant=bot.tenant,
        bot=bot,
        event_code="ADMIN_TEST_NOTIFICATION_SENT",
    )
    LOGGER.info(
        "telegram_test_notification_sent",
        extra={"event_code": "ADMIN_TEST_NOTIFICATION_SENT", "status": "SENT"},
    )
    return TestNotificationResult(
        status="SENT",
        message_id=int(message_id) if isinstance(message_id, int) else None,
    )


def deliver_alert_event(
    event: AlertEvent,
    *,
    client: TelegramApiClient | None = None,
    now: datetime | None = None,
) -> DeliveryResult:
    current = now or timezone.now()
    delivery, bot, action = _reserve_delivery(event, now=current)
    if action == "SENT":
        return DeliveryResult(status="SENT", delivery_id=delivery.pk, message_id=delivery.message_id)
    if action == "BUSY":
        return DeliveryResult(status="BUSY", delivery_id=delivery.pk)
    if action == "BLOCKED":
        return DeliveryResult(
            status="BLOCKED",
            delivery_id=delivery.pk,
            error_code=delivery.error_code,
        )
    if bot is None or bot.chat_id is None:
        return DeliveryResult(status="BLOCKED", delivery_id=delivery.pk, error_code="BOT_NOT_CONNECTED")

    try:
        telegram_client = client or _client_for(bot)
        message = telegram_client.send_message(
            bot.chat_id,
            _alert_text(event),
            reply_markup=_reply_markup(event),
        )
    except (TelegramApiError, TelegramTransportError, RuntimeError) as exc:
        error_code = _record_api_failure(bot, exc)
        delivery.status = NotificationDeliveryStatus.FAILED
        delivery.error_code = error_code
        delivery.error_message = "Falha segura ao entregar o alerta."
        delivery.save(update_fields=["status", "error_code", "error_message"])
        LOGGER.warning(
            "telegram_alert_delivery_failed",
            extra={
                "event_code": error_code,
                "status": NotificationDeliveryStatus.FAILED,
            },
        )
        return DeliveryResult(
            status="FAILED",
            delivery_id=delivery.pk,
            error_code=error_code,
        )

    message_id = message.get("message_id") if isinstance(message, dict) else None
    delivery.status = NotificationDeliveryStatus.SENT
    delivery.message_id = int(message_id) if isinstance(message_id, int) else None
    delivery.error_code = ""
    delivery.error_message = ""
    delivery.save(update_fields=["status", "message_id", "error_code", "error_message"])
    bot.last_seen_at = current
    bot.last_error_code = ""
    bot.save(update_fields=["last_seen_at", "last_error_code", "updated_at"])
    LOGGER.info(
        "telegram_alert_delivered",
        extra={
            "event_code": "TELEGRAM_ALERT_SENT",
            "status": NotificationDeliveryStatus.SENT,
        },
    )
    return DeliveryResult(
        status="SENT",
        delivery_id=delivery.pk,
        message_id=delivery.message_id,
    )


def deliver_pending_alerts(
    *,
    limit: int = 100,
    client_factory: Callable[[ManagedBot], TelegramApiClient] | None = None,
    now: datetime | None = None,
) -> list[DeliveryResult]:
    if limit < 1:
        raise ValueError("limit deve ser positivo")
    events = (
        AlertEvent.objects.filter(status="CREATED")
        .exclude(deliveries__channel=PERSONAL_CHANNEL, deliveries__status=NotificationDeliveryStatus.SENT)
        .select_related(
            "product",
            "observation__source",
            "observation__run",
        )
        .order_by("occurred_at", "id")[:limit]
    )
    results: list[DeliveryResult] = []
    for event in events:
        client = None
        if client_factory:
            bot = ManagedBot.objects.filter(tenant_id=event.tenant_id).first()
            if bot is not None:
                client = client_factory(bot)
        results.append(deliver_alert_event(event, client=client, now=now))
    return results
