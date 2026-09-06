from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from app.alerts import (
    BACK_IN_STOCK,
    EVENT_LABELS,
    NEW_HISTORICAL_LOW,
    PRICE_DROP,
    PRICE_INCREASE,
    SIGNIFICANT_INCREASE,
    TARGET_REACHED,
)
from monitoring.models import (
    AlertEvent,
    AlertRule,
    MonitoredProduct,
    MonitoringRun,
    PriceObservation,
    RunStatus,
)
from tenancy.models import DeliveryProfile


_EVENT_PRIORITY = (
    NEW_HISTORICAL_LOW,
    TARGET_REACHED,
    BACK_IN_STOCK,
    PRICE_DROP,
    PRICE_INCREASE,
)


@dataclass(frozen=True, slots=True)
class AlertEvaluationResult:
    event: AlertEvent | None
    created: bool = False


def _valid_observations(queryset: QuerySet) -> QuerySet:
    return queryset.filter(
        status="OK",
        in_stock=True,
        total_price_cents__isnull=False,
    )


def _previous_run_for(observation: PriceObservation) -> MonitoringRun | None:
    queryset = (
        MonitoringRun.objects.filter(
            tenant_id=observation.tenant_id,
            status=RunStatus.SUCCESS,
            observations__product_id=observation.product_id,
        )
        .exclude(pk=observation.run_id)
        .distinct()
    )
    batch_id = observation.run.collection_batch_id
    if batch_id is not None:
        queryset = queryset.exclude(collection_batch_id=batch_id)
    return queryset.order_by("-started_at", "-id").first()


def _previous_best(
    observation: PriceObservation,
    previous_run: MonitoringRun | None,
) -> PriceObservation | None:
    if previous_run is None:
        return None
    return (
        _valid_observations(
            previous_run.observations.filter(product_id=observation.product_id)
        )
        .order_by("total_price_cents", "observed_at", "id")
        .first()
    )


def _previous_historical_low(
    observation: PriceObservation,
) -> int | None:
    queryset = _valid_observations(
        PriceObservation.objects.filter(
            tenant_id=observation.tenant_id,
            product_id=observation.product_id,
            run__status=RunStatus.SUCCESS,
        ).exclude(pk=observation.pk)
    )
    batch_id = observation.run.collection_batch_id
    if batch_id is not None:
        queryset = queryset.exclude(run__collection_batch_id=batch_id)
    else:
        queryset = queryset.exclude(run_id=observation.run_id)
    return queryset.order_by("total_price_cents", "observed_at", "id").values_list(
        "total_price_cents", flat=True
    ).first()


def _previous_stock_state(
    observation: PriceObservation,
    previous_run: MonitoringRun | None,
) -> bool | None:
    if previous_run is None:
        return None
    observations = previous_run.observations.filter(
        product_id=observation.product_id
    )
    if observations.filter(status="OK", in_stock=True).exists():
        return True
    if observations.filter(status="OUT_OF_STOCK").exists():
        return False
    return None


def _rule_for(product: MonitoredProduct) -> AlertRule:
    profile = DeliveryProfile.objects.filter(tenant_id=product.tenant_id).first()
    defaults = {
        "minimum_price_drop_percent": (
            profile.minimum_price_drop_percent if profile else Decimal("1.00")
        ),
        "alert_price_increase": profile.alert_price_increase if profile else False,
    }
    rule, _created = AlertRule.objects.get_or_create(
        tenant_id=product.tenant_id,
        product_id=product.pk,
        defaults=defaults,
    )
    return rule


def _variation_percent(current: int, previous: int | None) -> Decimal | None:
    if previous in (None, 0):
        return None
    return (
        (Decimal(current) - Decimal(previous))
        * Decimal("100")
        / Decimal(previous)
    ).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


def _evaluate_locked(
    observation: PriceObservation,
    *,
    now: datetime,
) -> AlertEvaluationResult:
    if observation.status != "OK" or not observation.in_stock:
        return AlertEvaluationResult(event=None)
    if observation.total_price_cents is None or observation.is_baseline:
        return AlertEvaluationResult(event=None)

    product = MonitoredProduct.objects.select_for_update().get(pk=observation.product_id)
    rule = _rule_for(product)
    if not rule.enabled:
        return AlertEvaluationResult(event=None)

    previous_run = _previous_run_for(observation)
    previous_observation = _previous_best(observation, previous_run)
    previous_price = (
        previous_observation.total_price_cents if previous_observation else None
    )
    historical_low = _previous_historical_low(observation)
    previous_stock = _previous_stock_state(observation, previous_run)
    current_price = observation.total_price_cents

    triggered: list[str] = []
    historical_variation = _variation_percent(current_price, historical_low)
    if (
        rule.alert_new_historical_low
        and historical_variation is not None
        and -historical_variation >= rule.minimum_price_drop_percent
    ):
        triggered.append(NEW_HISTORICAL_LOW)
    if (
        rule.alert_target_reached
        and product.target_price_cents is not None
        and current_price <= product.target_price_cents
        and (previous_price is None or previous_price > product.target_price_cents)
    ):
        triggered.append(TARGET_REACHED)
    if rule.alert_back_in_stock and previous_stock is False:
        triggered.append(BACK_IN_STOCK)

    variation = _variation_percent(current_price, previous_price)
    if (
        variation is not None
        and -variation >= rule.minimum_price_drop_percent
    ):
        triggered.append(PRICE_DROP)
    if (
        rule.alert_price_increase
        and variation is not None
        and variation >= SIGNIFICANT_INCREASE * Decimal("100")
    ):
        triggered.append(PRICE_INCREASE)

    events = tuple(event for event in _EVENT_PRIORITY if event in triggered)
    if not events:
        return AlertEvaluationResult(event=None)

    if rule.cooldown_minutes:
        cooldown_since = now - timedelta(minutes=rule.cooldown_minutes)
        if AlertEvent.objects.filter(
            tenant_id=observation.tenant_id,
            product_id=observation.product_id,
            occurred_at__gte=cooldown_since,
            status="CREATED",
        ).exists():
            return AlertEvaluationResult(event=None)

    event_type = "|".join(events)
    idempotency_key = (
        f"{observation.tenant_id}:{observation.product_id}:"
        f"{observation.pk}:{event_type}"
    )
    event, created = AlertEvent.objects.get_or_create(
        idempotency_key=idempotency_key,
        defaults={
            "tenant_id": observation.tenant_id,
            "product_id": observation.product_id,
            "observation_id": observation.pk,
            "event_type": event_type,
            "status": "CREATED",
            "occurred_at": observation.observed_at,
            "current_price_cents": current_price,
            "previous_price_cents": previous_price,
            "historical_low_cents": historical_low,
            "target_price_cents": product.target_price_cents,
            "variation_percent": variation,
            "reason": "; ".join(EVENT_LABELS[event] for event in events),
        },
    )
    return AlertEvaluationResult(event=event, created=created)


@transaction.atomic
def evaluate_price_observation(
    observation: PriceObservation,
    *,
    now: datetime | None = None,
) -> AlertEvent | None:
    """Avalia uma observacao privada e retorna o evento novo ou idempotente."""
    result = _evaluate_locked(observation, now=now or timezone.now())
    return result.event


@transaction.atomic
def evaluate_collection_alerts(
    observations_by_product: dict[object, Iterable[PriceObservation]],
    *,
    baseline_product_ids: set[object] | None = None,
    now: datetime | None = None,
) -> int:
    """Avalia somente a melhor oferta valida de cada produto do lote."""
    created_count = 0
    baseline_ids = baseline_product_ids or set()
    for product_id, observations in observations_by_product.items():
        if product_id in baseline_ids:
            continue
        candidates = [
            observation
            for observation in observations
            if observation.status == "OK"
            and observation.in_stock
            and observation.total_price_cents is not None
        ]
        if not candidates:
            continue
        current = min(
            candidates,
            key=lambda observation: (
                observation.total_price_cents,
                observation.observed_at,
                observation.pk,
            ),
        )
        result = _evaluate_locked(current, now=now or timezone.now())
        if result.created:
            created_count += 1
    return created_count
