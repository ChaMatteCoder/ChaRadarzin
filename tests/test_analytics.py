from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.analytics import analyze_product, render_history_svg
from app.models import PriceHistoryPoint, Product


PRODUCT = Product(
    product_id="TEST001",
    active=True,
    name="Monitor Teste",
    exact_model="MODEL-1",
    target_price=Decimal("90"),
    payment_method="PIX",
)


def points(values: list[str], *, method: str = "PIX") -> tuple[PriceHistoryPoint, ...]:
    start = datetime(2026, 1, 1, 9, tzinfo=timezone.utc)
    return tuple(
        PriceHistoryPoint(
            observed_at=start + timedelta(days=index),
            price=Decimal(value),
            store="Loja A",
            payment_method=method,
        )
        for index, value in enumerate(values)
    )


class AnalyticsTest(unittest.TestCase):
    def test_calculates_descriptive_metrics_and_falling_trend(self) -> None:
        history = points([str(value) for value in range(100, 90, -1)])

        analysis = analyze_product(PRODUCT, history, history)

        self.assertEqual(analysis.sample_count, 10)
        self.assertEqual(analysis.minimum, Decimal("91"))
        self.assertEqual(analysis.maximum, Decimal("100"))
        self.assertEqual(analysis.average, Decimal("95.5"))
        self.assertEqual(analysis.median, Decimal("95.5"))
        self.assertEqual(analysis.moving_average_7, Decimal("94"))
        self.assertIsNone(analysis.moving_average_30)
        self.assertEqual(analysis.below_target_frequency, Decimal("0"))
        self.assertEqual(analysis.trend, "Queda")
        self.assertTrue(analysis.chart_ready)

    def test_blocks_prediction_when_history_is_sparse(self) -> None:
        history = points(["100"])

        analysis = analyze_product(PRODUCT, history, history)

        self.assertIsNone(analysis.forecast_7_days)
        self.assertIn("60 dias", analysis.forecast_status)
        self.assertFalse(analysis.chart_ready)

    def test_detects_atypical_promotion_against_recent_median(self) -> None:
        history = points(["100"] * 8 + ["80"])

        analysis = analyze_product(PRODUCT, history, history)

        self.assertTrue(analysis.atypical_promotion)
        self.assertEqual(analysis.atypical_discount, Decimal("0.2"))

    def test_constant_history_is_stable_instead_of_insufficient(self) -> None:
        history = points(["100"] * 7)

        analysis = analyze_product(PRODUCT, history, history)

        self.assertEqual(analysis.trend, "Estavel")
        self.assertEqual(analysis.trend_per_day, Decimal("0"))

    def test_unlocks_conservative_forecast_after_sufficient_history(self) -> None:
        history = points(
            [str(Decimal("150") - Decimal(index) / Decimal("2")) for index in range(90)]
        )

        analysis = analyze_product(PRODUCT, history, history)

        self.assertIsNotNone(analysis.forecast_7_days)
        self.assertIn("Estimativa linear", analysis.forecast_status)

    def test_renders_accessible_svg_only_with_sufficient_points(self) -> None:
        history = points([str(value) for value in range(100, 91, -1)])
        analysis = analyze_product(PRODUCT, history, history)

        svg = render_history_svg(analysis, PRODUCT)

        self.assertIn("<svg", svg)
        self.assertIn("Historico de preco total", svg)
        self.assertIn("Preco-alvo", svg)
        self.assertIn("9 dias", svg)

    def test_compares_payment_methods_and_calculates_store_volatility(self) -> None:
        history = points(["100", "90"])
        store_history = history + tuple(
            PriceHistoryPoint(
                observed_at=point.observed_at,
                price=point.price + Decimal("10"),
                store="Loja B",
                payment_method="CARTAO",
            )
            for point in history
        )

        analysis = analyze_product(PRODUCT, history, store_history)

        self.assertEqual(
            {item.payment_method for item in analysis.payment_stats},
            {"PIX", "CARTAO"},
        )
        self.assertTrue(
            all(item.coefficient is not None for item in analysis.store_volatility)
        )

    def test_requires_two_comparable_black_friday_seasons(self) -> None:
        raw: list[PriceHistoryPoint] = []
        for year, event_day in ((2024, 29), (2025, 28)):
            for day in (1, 10, 20):
                raw.append(
                    PriceHistoryPoint(
                        observed_at=datetime(year, 11, day, 9, tzinfo=timezone.utc),
                        price=Decimal("100"),
                        store="Loja A",
                        payment_method="PIX",
                    )
                )
            for month, day in ((11, 25), (11, event_day), (12, 2)):
                raw.append(
                    PriceHistoryPoint(
                        observed_at=datetime(year, month, day, 9, tzinfo=timezone.utc),
                        price=Decimal("80"),
                        store="Loja A",
                        payment_method="PIX",
                    )
                )
        history = tuple(sorted(raw, key=lambda point: point.observed_at))

        analysis = analyze_product(PRODUCT, history, history)

        self.assertEqual(analysis.black_friday_seasons, 2)
        self.assertEqual(analysis.black_friday_discount, Decimal("0.2"))


if __name__ == "__main__":
    unittest.main()
