from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from html import escape
from statistics import median

from app.models import PriceHistoryPoint, Product


CENT = Decimal("0.01")
MIN_TREND_SAMPLES = 7
MIN_CHART_SAMPLES = 8
MIN_ATYPICAL_REFERENCE_SAMPLES = 7
ATYPICAL_DISCOUNT = Decimal("0.10")
MIN_FORECAST_SAMPLES = 60
MIN_FORECAST_SPAN_DAYS = 90


@dataclass(frozen=True, slots=True)
class StoreVolatility:
    store: str
    samples: int
    average: Decimal
    standard_deviation: Decimal | None
    coefficient: Decimal | None


@dataclass(frozen=True, slots=True)
class PaymentStats:
    payment_method: str
    samples: int
    average: Decimal


@dataclass(frozen=True, slots=True)
class ProductAnalytics:
    product_id: str
    history: tuple[PriceHistoryPoint, ...]
    sample_count: int
    period_start: date | None
    period_end: date | None
    minimum: Decimal | None
    maximum: Decimal | None
    average: Decimal | None
    median: Decimal | None
    moving_average_7: Decimal | None
    moving_average_30: Decimal | None
    below_target_frequency: Decimal | None
    distance_from_low: Decimal | None
    trend: str
    trend_per_day: Decimal | None
    atypical_promotion: bool | None
    atypical_discount: Decimal | None
    forecast_7_days: Decimal | None
    forecast_status: str
    store_volatility: tuple[StoreVolatility, ...]
    payment_stats: tuple[PaymentStats, ...]
    black_friday_discount: Decimal | None
    black_friday_seasons: int

    @property
    def chart_ready(self) -> bool:
        return self.sample_count >= MIN_CHART_SAMPLES


def _mean(values: list[Decimal] | tuple[Decimal, ...]) -> Decimal:
    return sum(values, Decimal("0")) / Decimal(len(values))


def _linear_slope(points: tuple[PriceHistoryPoint, ...]) -> Decimal | None:
    if len(points) < 2:
        return None
    first_date = points[0].observed_at.date()
    x_values = [Decimal((point.observed_at.date() - first_date).days) for point in points]
    y_values = [point.price for point in points]
    x_mean = _mean(x_values)
    y_mean = _mean(y_values)
    denominator = sum((value - x_mean) ** 2 for value in x_values)
    if denominator == 0:
        return None
    return sum(
        (x_value - x_mean) * (y_value - y_mean)
        for x_value, y_value in zip(x_values, y_values)
    ) / denominator


def _store_volatility(
    store_history: tuple[PriceHistoryPoint, ...],
) -> tuple[StoreVolatility, ...]:
    grouped: dict[str, list[Decimal]] = {}
    for point in store_history:
        grouped.setdefault(point.store, []).append(point.price)
    result: list[StoreVolatility] = []
    for store, values in sorted(grouped.items(), key=lambda item: item[0].casefold()):
        average = _mean(values)
        standard_deviation = None
        coefficient = None
        if len(values) >= 2:
            variance = _mean([(value - average) ** 2 for value in values])
            standard_deviation = variance.sqrt()
            coefficient = standard_deviation / average if average else None
        result.append(
            StoreVolatility(
                store=store,
                samples=len(values),
                average=average,
                standard_deviation=standard_deviation,
                coefficient=coefficient,
            )
        )
    return tuple(result)


def _payment_stats(
    store_history: tuple[PriceHistoryPoint, ...],
) -> tuple[PaymentStats, ...]:
    best_by_day_method: dict[tuple[date, str], Decimal] = {}
    for point in store_history:
        key = (point.observed_at.date(), point.payment_method)
        current = best_by_day_method.get(key)
        if current is None or point.price < current:
            best_by_day_method[key] = point.price
    grouped: dict[str, list[Decimal]] = {}
    for (_, payment_method), price in best_by_day_method.items():
        grouped.setdefault(payment_method, []).append(price)
    return tuple(
        PaymentStats(method, len(values), _mean(values))
        for method, values in sorted(grouped.items())
    )


def _black_friday(year: int) -> date:
    first = date(year, 11, 1)
    first_thursday = first + timedelta(days=(3 - first.weekday()) % 7)
    thanksgiving = first_thursday + timedelta(days=21)
    return thanksgiving + timedelta(days=1)


def _black_friday_seasonality(
    history: tuple[PriceHistoryPoint, ...],
) -> tuple[Decimal | None, int]:
    discounts: list[Decimal] = []
    years = sorted({point.observed_at.year for point in history})
    for year in years:
        event = _black_friday(year)
        event_prices = [
            point.price
            for point in history
            if event - timedelta(days=7) <= point.observed_at.date() <= event + timedelta(days=7)
        ]
        baseline_prices = [
            point.price
            for point in history
            if event - timedelta(days=37) <= point.observed_at.date() <= event - timedelta(days=8)
        ]
        if len(event_prices) < 3 or len(baseline_prices) < 3:
            continue
        baseline = median(baseline_prices)
        if baseline:
            discounts.append((baseline - median(event_prices)) / baseline)
    if len(discounts) < 2:
        return None, len(discounts)
    return _mean(discounts), len(discounts)


def analyze_product(
    product: Product,
    history: tuple[PriceHistoryPoint, ...],
    store_history: tuple[PriceHistoryPoint, ...],
) -> ProductAnalytics:
    ordered = tuple(sorted(history, key=lambda point: point.observed_at))
    prices = [point.price for point in ordered]
    if not prices:
        return ProductAnalytics(
            product_id=product.product_id,
            history=(),
            sample_count=0,
            period_start=None,
            period_end=None,
            minimum=None,
            maximum=None,
            average=None,
            median=None,
            moving_average_7=None,
            moving_average_30=None,
            below_target_frequency=None,
            distance_from_low=None,
            trend="Dados insuficientes",
            trend_per_day=None,
            atypical_promotion=None,
            atypical_discount=None,
            forecast_7_days=None,
            forecast_status="Indisponivel: ainda nao ha precos validos",
            store_volatility=(),
            payment_stats=(),
            black_friday_discount=None,
            black_friday_seasons=0,
        )

    average = _mean(prices)
    minimum = min(prices)
    current = prices[-1]
    span_days = (ordered[-1].observed_at.date() - ordered[0].observed_at.date()).days + 1
    trend_points = ordered[-30:]
    slope = _linear_slope(trend_points) if len(ordered) >= MIN_TREND_SAMPLES else None
    trend_per_day = (
        slope / _mean([point.price for point in trend_points])
        if slope is not None
        else None
    )
    if trend_per_day is None:
        trend = "Dados insuficientes (minimo de 7 dias)"
    elif trend_per_day <= Decimal("-0.001"):
        trend = "Queda"
    elif trend_per_day >= Decimal("0.001"):
        trend = "Alta"
    else:
        trend = "Estavel"

    reference = prices[-31:-1]
    atypical_promotion = None
    atypical_discount = None
    if len(reference) >= MIN_ATYPICAL_REFERENCE_SAMPLES:
        reference_median = median(reference)
        atypical_discount = (
            (reference_median - current) / reference_median
            if reference_median
            else Decimal("0")
        )
        atypical_promotion = atypical_discount >= ATYPICAL_DISCOUNT

    forecast = None
    if len(ordered) >= MIN_FORECAST_SAMPLES and span_days >= MIN_FORECAST_SPAN_DAYS:
        forecast_slope = _linear_slope(ordered[-90:])
        if forecast_slope is not None:
            forecast = max(Decimal("0"), current + forecast_slope * Decimal("7")).quantize(
                CENT, rounding=ROUND_HALF_UP
            )
            forecast_status = "Estimativa linear de 7 dias; use apenas como tendencia"
        else:
            forecast_status = "Indisponivel: datas insuficientes para estimar tendencia"
    else:
        forecast_status = (
            "Indisponivel: exige 60 dias coletados em uma janela de pelo menos 90 dias"
        )

    black_friday_discount, black_friday_seasons = _black_friday_seasonality(ordered)
    return ProductAnalytics(
        product_id=product.product_id,
        history=ordered,
        sample_count=len(prices),
        period_start=ordered[0].observed_at.date(),
        period_end=ordered[-1].observed_at.date(),
        minimum=minimum,
        maximum=max(prices),
        average=average,
        median=median(prices),
        moving_average_7=_mean(prices[-7:]) if len(prices) >= 7 else None,
        moving_average_30=_mean(prices[-30:]) if len(prices) >= 30 else None,
        below_target_frequency=(
            Decimal(sum(price <= product.target_price for price in prices))
            / Decimal(len(prices))
        ),
        distance_from_low=(current - minimum) / minimum if minimum else None,
        trend=trend,
        trend_per_day=trend_per_day,
        atypical_promotion=atypical_promotion,
        atypical_discount=atypical_discount,
        forecast_7_days=forecast,
        forecast_status=forecast_status,
        store_volatility=_store_volatility(store_history),
        payment_stats=_payment_stats(store_history),
        black_friday_discount=black_friday_discount,
        black_friday_seasons=black_friday_seasons,
    )


def render_history_svg(analysis: ProductAnalytics, product: Product) -> str:
    if not analysis.chart_ready:
        raise ValueError("Grafico exige pelo menos 8 pontos diarios")
    width, height = 960, 420
    left, right, top, bottom = 82, 30, 76, 58
    plot_width = width - left - right
    plot_height = height - top - bottom
    prices = [point.price for point in analysis.history]
    values = prices + [product.target_price]
    low, high = min(values), max(values)
    padding = max((high - low) * Decimal("0.08"), high * Decimal("0.01"), Decimal("1"))
    axis_low, axis_high = low - padding, high + padding
    axis_span = axis_high - axis_low

    def x(index: int) -> float:
        return left + (plot_width * index / max(1, len(prices) - 1))

    def y(value: Decimal) -> float:
        return top + float((axis_high - value) / axis_span) * plot_height

    def path_for(window: int) -> str:
        points: list[str] = []
        for index in range(window - 1, len(prices)):
            moving = _mean(prices[index - window + 1 : index + 1])
            points.append(f"{x(index):.1f},{y(moving):.1f}")
        return " ".join(points)

    price_points = " ".join(
        f"{x(index):.1f},{y(value):.1f}" for index, value in enumerate(prices)
    )
    title = escape(f"Historico de preco total — {product.name}")
    period = (
        f"{analysis.period_start.strftime('%d/%m/%Y')} a "
        f"{analysis.period_end.strftime('%d/%m/%Y')}"
    )
    subtitle = escape(
        f"Melhor oferta diaria | {period} | {analysis.sample_count} dias | eixo ajustado ao intervalo"
    )
    grid: list[str] = []
    for index in range(5):
        value = axis_high - axis_span * Decimal(index) / Decimal("4")
        y_position = y(value)
        label = f"R$ {value:,.0f}".replace(",", ".")
        grid.append(
            f'<line x1="{left}" y1="{y_position:.1f}" x2="{width-right}" y2="{y_position:.1f}" stroke="#E5E7EB"/>'
            f'<text x="{left-10}" y="{y_position+4:.1f}" text-anchor="end" class="axis">{label}</text>'
        )
    target_y = y(product.target_price)
    moving_7 = path_for(7) if len(prices) >= 7 else ""
    moving_30 = path_for(30) if len(prices) >= 30 else ""
    legend = [
        '<line x1="646" y1="33" x2="674" y2="33" stroke="#2563EB" stroke-width="3"/>',
        '<text x="681" y="37" class="legend">Preco diario</text>',
        '<line x1="782" y1="33" x2="810" y2="33" stroke="#4B5563" stroke-width="2" stroke-dasharray="6 5"/>',
        '<text x="817" y="37" class="legend">Preco-alvo</text>',
    ]
    if moving_7:
        legend.extend(
            [
                '<line x1="646" y1="53" x2="674" y2="53" stroke="#D97706" stroke-width="2"/>',
                '<text x="681" y="57" class="legend">Media 7d</text>',
            ]
        )
    if moving_30:
        legend.extend(
            [
                '<line x1="782" y1="53" x2="810" y2="53" stroke="#D97706" stroke-width="2" stroke-dasharray="3 4"/>',
                '<text x="817" y="57" class="legend">Media 30d</text>',
            ]
        )
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title description">',
        '<style>.title{font:600 18px Arial,sans-serif;fill:#111827}.subtitle,.axis,.legend{font:12px Arial,sans-serif;fill:#4B5563}.axis{font-variant-numeric:tabular-nums}</style>',
        f'<title id="title">{title}</title>',
        f'<desc id="description">{subtitle}</desc>',
        '<rect width="100%" height="100%" fill="#FFFFFF"/>',
        f'<text x="{left}" y="28" class="title">{title}</text>',
        f'<text x="{left}" y="49" class="subtitle">{subtitle}</text>',
        *legend,
        *grid,
        f'<line x1="{left}" y1="{target_y:.1f}" x2="{width-right}" y2="{target_y:.1f}" stroke="#4B5563" stroke-width="2" stroke-dasharray="6 5"/>',
        f'<polyline points="{price_points}" fill="none" stroke="#2563EB" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>',
    ]
    if moving_7:
        svg.append(
            f'<polyline points="{moving_7}" fill="none" stroke="#D97706" stroke-width="2" stroke-linejoin="round"/>'
        )
    if moving_30:
        svg.append(
            f'<polyline points="{moving_30}" fill="none" stroke="#D97706" stroke-width="2" stroke-dasharray="3 4" stroke-linejoin="round"/>'
        )
    first_label = analysis.period_start.strftime("%d/%m")
    last_label = analysis.period_end.strftime("%d/%m")
    svg.extend(
        [
            f'<text x="{left}" y="{height-24}" text-anchor="start" class="axis">{first_label}</text>',
            f'<text x="{width-right}" y="{height-24}" text-anchor="end" class="axis">{last_label}</text>',
            '</svg>',
        ]
    )
    return "\n".join(svg)
