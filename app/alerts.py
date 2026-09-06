from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.models import ProductSummary


MINIMUM_DROP = Decimal("0.01")
SIGNIFICANT_INCREASE = Decimal("0.05")

NEW_HISTORICAL_LOW = "NEW_HISTORICAL_LOW"
TARGET_REACHED = "TARGET_REACHED"
BACK_IN_STOCK = "BACK_IN_STOCK"
PRICE_DROP = "PRICE_DROP"
PRICE_INCREASE = "PRICE_INCREASE"

EVENT_LABELS = {
    NEW_HISTORICAL_LOW: "🔥 Novo menor preço histórico",
    TARGET_REACHED: "🎯 Preço-alvo atingido",
    BACK_IN_STOCK: "📦 Produto voltou ao estoque",
    PRICE_DROP: "🔻 Queda de pelo menos 1%",
    PRICE_INCREASE: "⚠️ Aumento de pelo menos 5%",
}

EVENT_HEADLINES = {
    NEW_HISTORICAL_LOW: "🔥 NOVO MENOR PREÇO",
    TARGET_REACHED: "🎯 PREÇO-ALVO ATINGIDO",
    BACK_IN_STOCK: "📦 PRODUTO DE VOLTA AO ESTOQUE",
    PRICE_DROP: "🔻 QUEDA DE PREÇO",
    PRICE_INCREASE: "⚠️ AUMENTO DE PREÇO",
}

_EVENT_PRIORITY = (
    NEW_HISTORICAL_LOW,
    TARGET_REACHED,
    BACK_IN_STOCK,
    PRICE_DROP,
    PRICE_INCREASE,
)


@dataclass(frozen=True, slots=True)
class AlertDecision:
    events: tuple[str, ...]
    previous_price: Decimal | None

    @property
    def event_type(self) -> str:
        return "|".join(self.events)

    @property
    def headline(self) -> str:
        for event in _EVENT_PRIORITY:
            if event in self.events:
                return EVENT_HEADLINES[event]
        raise ValueError("Decisao de alerta sem evento conhecido")

    @classmethod
    def from_event_type(
        cls, event_type: str, previous_price: Decimal | None
    ) -> AlertDecision:
        events = tuple(
            event for event in event_type.split("|") if event in EVENT_LABELS
        )
        if not events:
            raise ValueError("Tipo de alerta armazenado e invalido")
        return cls(events=events, previous_price=previous_price)


def evaluate_alert(
    summary: ProductSummary,
    *,
    previous_historical_low: Decimal | None,
    previous_in_stock: bool | None,
    alert_price_increase: bool = False,
) -> AlertDecision | None:
    current = summary.best_price
    previous = summary.previous_best_total
    if current is None:
        return None

    # A primeira oferta valida estabelece a baseline. Eventos de preco so podem
    # ser avaliados depois que existe algum estado historico comparavel.
    if (
        previous is None
        and previous_historical_low is None
        and previous_in_stock is None
    ):
        return None

    triggered: set[str] = set()
    if previous_historical_low is not None and current < previous_historical_low:
        triggered.add(NEW_HISTORICAL_LOW)
    if current <= summary.product.target_price and (
        previous is None or previous > summary.product.target_price
    ):
        triggered.add(TARGET_REACHED)
    if previous_in_stock is False:
        triggered.add(BACK_IN_STOCK)

    if previous not in (None, Decimal("0")):
        change = (current - previous) / previous
        if change <= -MINIMUM_DROP:
            triggered.add(PRICE_DROP)
        if alert_price_increase and change >= SIGNIFICANT_INCREASE:
            triggered.add(PRICE_INCREASE)

    events = tuple(event for event in _EVENT_PRIORITY if event in triggered)
    if not events:
        return None
    return AlertDecision(events=events, previous_price=previous)
