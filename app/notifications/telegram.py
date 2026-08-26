from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from decimal import Decimal
from html import escape
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.alerts import AlertDecision, EVENT_LABELS
from app.models import ProductSummary


class TelegramError(RuntimeError):
    pass


TelegramRequester = Callable[..., dict[str, Any]]


def _money(value: Decimal | None) -> str:
    if value is None:
        return "—"
    formatted = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {formatted}"


def _telegram_post(request: Request, *, timeout: float) -> dict[str, Any]:
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            description = str(payload.get("description") or "erro HTTP")
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
            description = f"erro HTTP {exc.code}"
        raise TelegramError(f"Telegram recusou a mensagem: {description}") from exc
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise TelegramError("Falha de rede ao enviar mensagem ao Telegram") from exc
    if not isinstance(payload, dict):
        raise TelegramError("Resposta inesperada da API do Telegram")
    return payload


class TelegramClient:
    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        *,
        timeout: float = 20.0,
        retries: int = 1,
        requester: TelegramRequester = _telegram_post,
    ) -> None:
        if not re.fullmatch(r"\d+:[A-Za-z0-9_-]{20,}", bot_token):
            raise ValueError("TELEGRAM_BOT_TOKEN possui formato invalido")
        if not chat_id.strip():
            raise ValueError("TELEGRAM_CHAT_ID nao pode ficar vazio")
        self._bot_token = bot_token
        self.chat_id = chat_id.strip()
        self.timeout = timeout
        self.retries = retries
        self.requester = requester

    def send_message(self, text: str) -> int | None:
        if not 1 <= len(text) <= 4096:
            raise ValueError("Mensagem do Telegram deve conter entre 1 e 4096 caracteres")
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        request = Request(
            f"https://api.telegram.org/bot{self._bot_token}/sendMessage",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        last_error: TelegramError | None = None
        for attempt in range(self.retries + 1):
            try:
                response = self.requester(request, timeout=self.timeout)
                if response.get("ok") is not True:
                    raise TelegramError(
                        "Telegram recusou a mensagem: "
                        + str(response.get("description") or "erro desconhecido")
                    )
                result = response.get("result") or {}
                message_id = result.get("message_id")
                return int(message_id) if message_id is not None else None
            except TelegramError as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(0.6 * (attempt + 1))
        raise last_error or TelegramError("Falha desconhecida ao enviar mensagem")


def format_price_alert(summary: ProductSummary, decision: AlertDecision) -> str:
    offer = summary.best_offer
    if offer is None or summary.best_price is None:
        raise ValueError("Resumo sem oferta comparavel para notificacao")
    previous = decision.previous_price
    change = (
        (summary.best_price - previous) / previous
        if previous not in (None, Decimal("0"))
        else None
    )
    if offer.delivery_min_days is not None and offer.delivery_max_days is not None:
        delivery = f"{offer.delivery_min_days} a {offer.delivery_max_days} dias"
    elif offer.delivery_max_days is not None:
        delivery = f"até {offer.delivery_max_days} dias"
    else:
        delivery = "não informado"
    lines = [
        f"<b>{decision.headline}</b>",
        "",
        f"<b>{escape(summary.product.name)}</b>",
        f"Modelo: {escape(summary.product.exact_model)}",
    ]
    if previous is not None:
        lines.append(f"Antes: {_money(previous)}")
    lines.extend(
        [
            f"Agora: <b>{_money(summary.best_price)}</b>",
            f"Melhor loja: {escape(offer.store)}",
            f"Produto: {_money(offer.product_price)}",
            f"Frete: {_money(offer.shipping_price)}",
        ]
    )
    if change is not None and change < 0:
        lines.append(f"Queda: {abs(change) * 100:.1f}%".replace(".", ","))
    elif change is not None and change > 0:
        lines.append(f"Aumento: {change * 100:.1f}%".replace(".", ","))
    lines.extend(
        [
            f"Preço-alvo: {_money(summary.product.target_price)}",
            f"Menor histórico: {_money(summary.historical_low)}",
            f"Entrega: {delivery}",
        ]
    )
    if len(decision.events) > 1:
        lines.extend(
            ["", "Motivos:", *(EVENT_LABELS[event] for event in decision.events)]
        )
    lines.extend(
        ["", f'<a href="{escape(offer.url, quote=True)}">Abrir oferta</a>']
    )
    return "\n".join(lines)


def format_test_message() -> str:
    return "✅ <b>Radar de Preços conectado</b>\n\nAs notificações do Telegram estão funcionando."
