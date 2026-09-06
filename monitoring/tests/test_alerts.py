from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from monitoring.alerts import evaluate_collection_alerts, evaluate_price_observation
from monitoring.models import (
    AlertEvent,
    AlertRule,
    CollectionBatch,
    MonitoredProduct,
    MonitoringRun,
    OfferSource,
    PriceObservation,
    RunStatus,
    SupportedStore,
)
from tenancy.models import DeliveryProfile, Tenant


def aware(year: int, month: int, day: int, hour: int = 10) -> datetime:
    return timezone.make_aware(
        datetime(year, month, day, hour),
        timezone.get_current_timezone(),
    )


class AlertEngineTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant Alertas")
        DeliveryProfile.objects.create(
            tenant=self.tenant,
            postal_code="01001000",
            minimum_price_drop_percent=Decimal("1.00"),
        )
        self.product = MonitoredProduct.objects.create(
            tenant=self.tenant,
            name="SSD de teste",
            exact_model="CT1000BX500SSD1",
            variant="1 TB / SATA",
            target_price_cents=95000,
        )
        self.source = OfferSource.objects.create(
            tenant=self.tenant,
            product=self.product,
            store=SupportedStore.AMAZON,
            url="https://www.amazon.com.br/dp/B07YD579WM",
            expected_seller="BPS Oficial",
        )

    def _observation(
        self,
        price: int | None,
        when: datetime,
        *,
        in_stock: bool = True,
        status: str = "OK",
        baseline: bool = False,
    ) -> PriceObservation:
        batch = CollectionBatch.objects.create(started_at=when)
        run = MonitoringRun.objects.create(
            tenant=self.tenant,
            collection_batch=batch,
            started_at=when,
            status=RunStatus.SUCCESS,
            mode="SHARED_COLLECTION",
            input_file_name="web-panel",
        )
        return PriceObservation.objects.create(
            tenant=self.tenant,
            run=run,
            product=self.product,
            source=self.source,
            observed_at=when,
            seller="BPS Oficial",
            payment_method="PIX",
            product_price_cents=price,
            shipping_price_cents=0 if price is not None else None,
            total_price_cents=price,
            in_stock=in_stock,
            status=status,
            parser_version="test",
            is_baseline=baseline,
        )

    def test_first_observation_only_establishes_baseline(self):
        observation = self._observation(100000, aware(2026, 8, 1), baseline=True)

        self.assertIsNone(evaluate_price_observation(observation))
        self.assertEqual(AlertEvent.objects.count(), 0)

    def test_reduction_creates_one_idempotent_event(self):
        self._observation(100000, aware(2026, 8, 1), baseline=True)
        current = self._observation(98000, aware(2026, 8, 2))

        first = evaluate_price_observation(current)
        second = evaluate_price_observation(current)

        self.assertIsNotNone(first)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(AlertEvent.objects.count(), 1)
        self.assertIn("PRICE_DROP", first.event_type)
        self.assertEqual(first.previous_price_cents, 100000)
        self.assertEqual(first.variation_percent, Decimal("-2.000"))

    def test_irrelevant_oscillation_does_not_create_event(self):
        self._observation(100000, aware(2026, 8, 1), baseline=True)
        current = self._observation(99500, aware(2026, 8, 2))

        self.assertIsNone(evaluate_price_observation(current))
        self.assertEqual(AlertEvent.objects.count(), 0)

    def test_historical_low_and_target_are_consolidated(self):
        self._observation(100000, aware(2026, 8, 1), baseline=True)
        current = self._observation(94000, aware(2026, 8, 2))

        event = evaluate_price_observation(current)

        self.assertIsNotNone(event)
        self.assertEqual(AlertEvent.objects.count(), 1)
        self.assertEqual(
            set(event.event_type.split("|")),
            {"NEW_HISTORICAL_LOW", "TARGET_REACHED", "PRICE_DROP"},
        )
        self.assertEqual(event.historical_low_cents, 100000)
        self.assertEqual(event.target_price_cents, 95000)

    def test_return_to_stock_alerts_after_previous_out_of_stock(self):
        self._observation(
            None,
            aware(2026, 8, 1),
            in_stock=False,
            status="OUT_OF_STOCK",
        )
        current = self._observation(100000, aware(2026, 8, 2))

        event = evaluate_price_observation(current)

        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, "BACK_IN_STOCK")
        self.assertIsNone(event.previous_price_cents)

    def test_profile_threshold_is_used_for_new_rule(self):
        DeliveryProfile.objects.filter(tenant=self.tenant).update(
            minimum_price_drop_percent=Decimal("2.00")
        )
        self._observation(100000, aware(2026, 8, 1), baseline=True)
        current = self._observation(98500, aware(2026, 8, 2))

        self.assertIsNone(evaluate_price_observation(current))
        self.assertEqual(AlertRule.objects.get(product=self.product).minimum_price_drop_percent, Decimal("2.00"))

        AlertRule.objects.filter(product=self.product).update(
            alert_new_historical_low=False,
            alert_target_reached=False,
        )
        current = self._observation(97000, aware(2026, 8, 3))
        self.assertIsNone(evaluate_price_observation(current))
        current = self._observation(94000, aware(2026, 8, 4))
        self.assertIsNotNone(evaluate_price_observation(current))

    def test_cooldown_suppresses_a_second_event(self):
        self._observation(100000, aware(2026, 8, 1), baseline=True)
        first = self._observation(98000, aware(2026, 8, 2, 10))
        self.assertIsNotNone(evaluate_price_observation(first, now=aware(2026, 8, 2, 10)))
        second = self._observation(97000, aware(2026, 8, 2, 12))

        self.assertIsNone(evaluate_price_observation(second, now=aware(2026, 8, 2, 12)))
        self.assertEqual(AlertEvent.objects.count(), 1)

    def test_collection_evaluator_chooses_best_offer_and_skips_baseline(self):
        baseline = self._observation(100000, aware(2026, 8, 1), baseline=True)
        current_expensive = self._observation(99000, aware(2026, 8, 2))
        current_best = self._observation(97000, aware(2026, 8, 2))

        created = evaluate_collection_alerts(
            {self.product.pk: [current_expensive, current_best]},
            now=aware(2026, 8, 2),
        )

        self.assertEqual(created, 1)
        event = AlertEvent.objects.get()
        self.assertEqual(event.observation_id, current_best.pk)
        self.assertNotEqual(event.observation_id, baseline.pk)

    def test_failed_run_is_ignored_when_comparing_prices(self):
        self._observation(100000, aware(2026, 8, 1), baseline=True)
        failed = self._observation(50000, aware(2026, 8, 2))
        MonitoringRun.objects.filter(pk=failed.run_id).update(status=RunStatus.FAILED)
        current = self._observation(98000, aware(2026, 8, 3))

        event = evaluate_price_observation(current)

        self.assertIsNotNone(event)
        self.assertEqual(event.previous_price_cents, 100000)
        self.assertEqual(event.historical_low_cents, 100000)
