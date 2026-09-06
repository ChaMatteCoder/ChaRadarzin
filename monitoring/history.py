from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Iterable

from django.utils import timezone

from monitoring.models import MonitoredProduct, OfferSource, PriceObservation, RunStatus


VALID_PRICE_STATUSES = ("OK",)


@dataclass(frozen=True, slots=True)
class HistoryRow:
    day: date
    day_label: str
    total_price_cents: int
    source_name: str


@dataclass(frozen=True, slots=True)
class HistoryPoint:
    day_label: str
    total_price_cents: int
    x: float
    y: float
    show_label: bool


@dataclass(frozen=True, slots=True)
class ProductHistory:
    observations: tuple[PriceObservation, ...]
    current_offers: tuple[PriceObservation, ...]
    best_offer: PriceObservation | None
    historical_low_cents: int | None
    previous_best_cents: int | None
    variation_percent: Decimal | None
    rows: tuple[HistoryRow, ...]
    points: tuple[HistoryPoint, ...]
    chart_points: str
    chart_area_points: str

    @property
    def has_history(self) -> bool:
        return bool(self.rows)

    @property
    def has_comparison(self) -> bool:
        return self.variation_percent is not None


def _day_label(day: date) -> str:
    return day.strftime("%d/%m")


def _variation_percent(current: int | None, previous: int | None) -> Decimal | None:
    if current is None or previous in (None, 0):
        return None
    try:
        return ((Decimal(current) - Decimal(previous)) / Decimal(previous) * 100).quantize(
            Decimal("0.1")
        )
    except (InvalidOperation, ZeroDivisionError):
        return None


def _svg_points(rows: list[HistoryRow]) -> tuple[tuple[HistoryPoint, ...], str, str]:
    if not rows:
        return (), "", ""
    values = [row.total_price_cents for row in rows]
    minimum = min(values)
    maximum = max(values)
    spread = maximum - minimum
    points: list[HistoryPoint] = []
    for index, row in enumerate(rows):
        x = 8.0 if len(rows) == 1 else 8.0 + 84.0 * index / (len(rows) - 1)
        normalized = 0.5 if spread == 0 else (row.total_price_cents - minimum) / spread
        y = 88.0 - normalized * 68.0
        points.append(
            HistoryPoint(
                day_label=row.day_label,
                total_price_cents=row.total_price_cents,
                x=round(x, 1),
                y=round(y, 1),
                show_label=(
                    len(rows) <= 8
                    or index == 0
                    or index == len(rows) - 1
                    or index % max(1, (len(rows) - 1) // 5) == 0
                ),
            )
        )
    line = " ".join(f"{point.x:.1f},{point.y:.1f}" for point in points)
    area = f"8,100 {line} 92,100"
    return tuple(points), line, area


def build_product_history(
    product: MonitoredProduct,
    *,
    sources: Iterable[OfferSource] | None = None,
    limit: int = 365,
) -> ProductHistory:
    """Build a tenant-scoped current-offer and daily-history summary."""
    if limit < 1:
        raise ValueError("limit deve ser positivo")
    source_list = list(sources) if sources is not None else list(
        OfferSource.objects.filter(
            tenant_id=product.tenant_id,
            product_id=product.pk,
            active=True,
        )
    )
    active_source_ids = {source.pk for source in source_list}
    latest_candidates = (
        PriceObservation.objects.filter(
            tenant_id=product.tenant_id,
            product_id=product.pk,
            source_id__in=active_source_ids,
            run__status=RunStatus.SUCCESS,
        )
        .select_related("source")
        .order_by("-observed_at", "-id")
    )
    latest_by_source: dict[object, PriceObservation] = {}
    for observation in latest_candidates:
        latest_by_source.setdefault(observation.source_id, observation)
    observations = tuple(
        PriceObservation.objects.filter(
            tenant_id=product.tenant_id,
            product_id=product.pk,
            status__in=VALID_PRICE_STATUSES,
            total_price_cents__isnull=False,
            run__status=RunStatus.SUCCESS,
        )
        .select_related("source")
        .order_by("-observed_at", "-id")[:limit]
    )

    current_offers = tuple(
        sorted(
            (
                observation
                for observation in latest_by_source.values()
                if observation.status in VALID_PRICE_STATUSES
                and observation.total_price_cents is not None
            ),
            key=lambda item: (item.total_price_cents, item.observed_at, str(item.pk)),
        )
    )
    best_offer = current_offers[0] if current_offers else None

    daily_best: dict[date, PriceObservation] = {}
    for observation in observations:
        day = timezone.localtime(observation.observed_at).date()
        current = daily_best.get(day)
        if current is None or observation.total_price_cents < current.total_price_cents:
            daily_best[day] = observation
    sorted_days = sorted(daily_best)
    rows = tuple(
        HistoryRow(
            day=day,
            day_label=_day_label(day),
            total_price_cents=daily_best[day].total_price_cents,
            source_name=daily_best[day].source.store,
        )
        for day in reversed(sorted_days)
    )
    chart_rows = [
        HistoryRow(
            day=day,
            day_label=_day_label(day),
            total_price_cents=daily_best[day].total_price_cents,
            source_name=daily_best[day].source.store,
        )
        for day in sorted_days[-30:]
    ]
    points, chart_points, chart_area_points = _svg_points(chart_rows)
    historical_low = min((row.total_price_cents for row in rows), default=None)
    previous_best = None
    if best_offer is not None:
        best_day = timezone.localtime(best_offer.observed_at).date()
        prior_days = [day for day in sorted_days if day < best_day]
        if prior_days:
            previous_best = daily_best[prior_days[-1]].total_price_cents
    current_price = best_offer.total_price_cents if best_offer else None
    return ProductHistory(
        observations=observations,
        current_offers=current_offers,
        best_offer=best_offer,
        historical_low_cents=historical_low,
        previous_best_cents=previous_best,
        variation_percent=_variation_percent(current_price, previous_best),
        rows=rows,
        points=points,
        chart_points=chart_points,
        chart_area_points=chart_area_points,
    )
