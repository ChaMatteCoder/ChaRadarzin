from __future__ import annotations

from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import Mock

from django.core.management import call_command
from django.test import TestCase

from app.shipping import ShippingQuote
from monitoring.collection_engine import (
    CollectionExecutionError,
    StoreRateLimiter,
    collect_active_offers,
)
from monitoring.models import (
    AlertEvent,
    CollectionBatch,
    CollectionBatchStatus,
    CollectorError,
    MonitoredProduct,
    MonitoringRun,
    OfferSource,
    PriceObservation,
    SharedOffer,
    SharedOfferObservation,
)
from monitoring.preview import ProductPreviewError
from tenancy.models import DeliveryProfile, Tenant


FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"
AMAZON_HTML = (FIXTURES / "amazon" / "product_in_stock.html").read_text(
    encoding="utf-8"
)
KABUM_HTML = (FIXTURES / "kabum" / "product_in_stock.html").read_text(
    encoding="utf-8"
)
AMAZON_OUT_OF_STOCK_HTML = AMAZON_HTML.replace(
    "Em estoque",
    "Temporariamente fora de estoque",
)


def no_wait_limiter() -> StoreRateLimiter:
    return StoreRateLimiter(1_000_000, sleeper=lambda _seconds: None)


class SharedCollectionEngineTest(TestCase):
    def _tenant_source(
        self,
        suffix: str,
        *,
        url: str = "https://www.amazon.com.br/dp/B07YD579WM",
        model: str = "CT1000BX500SSD1",
        postal_code: str | None = "01001000",
        payment_method: str = "PIX",
    ) -> tuple[Tenant, MonitoredProduct, OfferSource]:
        tenant = Tenant.objects.create(name=f"Tenant {suffix}")
        if postal_code is not None:
            DeliveryProfile.objects.create(
                tenant=tenant,
                postal_code=postal_code,
                preferred_payment_method=payment_method,
            )
        product = MonitoredProduct.objects.create(
            tenant=tenant,
            name=f"SSD {suffix}",
            exact_model=model,
            variant="1 TB / SATA",
            preferred_payment_method=payment_method,
        )
        source = OfferSource.objects.create(
            tenant=tenant,
            product=product,
            store="Amazon",
            url=url,
            expected_seller="BPS Oficial",
            expected_variant="1 TB / SATA",
        )
        return tenant, product, source

    def test_same_canonical_url_is_fetched_once_for_two_tenants(self):
        tenant_a, product_a, source_a = self._tenant_source(
            "A",
            url="https://www.amazon.com.br/gp/product/B07YD579WM?ref_=tracking",
            postal_code="01001000",
        )
        tenant_b, product_b, source_b = self._tenant_source(
            "B",
            url="https://www.amazon.com.br/dp/B07YD579WM",
            postal_code="20040002",
        )
        fetcher = Mock(return_value=AMAZON_HTML)
        shipping_calls: list[str] = []

        def shipping_quoter(_product, _link, postal_code, _html, **_kwargs):
            shipping_calls.append(postal_code)
            price = Decimal("10.00") if postal_code == "01001000" else Decimal("20.00")
            return ShippingQuote(price=price, delivery_min_days=2, delivery_max_days=4)

        result = collect_active_offers(
            fetcher=fetcher,
            shipping_quoter=shipping_quoter,
            rate_limiter=no_wait_limiter(),
        )

        self.assertEqual(result.status, CollectionBatchStatus.SUCCESS)
        self.assertEqual(result.urls_planned, 1)
        self.assertEqual(result.urls_fetched, 1)
        self.assertEqual(result.sources_processed, 2)
        self.assertEqual(result.tenant_runs_count, 2)
        self.assertEqual(result.baselines_created, 2)
        fetcher.assert_called_once_with("https://www.amazon.com.br/dp/B07YD579WM")
        self.assertCountEqual(shipping_calls, ["01001000", "20040002"])
        self.assertEqual(SharedOffer.objects.count(), 1)
        self.assertEqual(SharedOfferObservation.objects.count(), 1)
        self.assertEqual(PriceObservation.objects.count(), 2)
        self.assertEqual(MonitoringRun.objects.count(), 2)

        source_a.refresh_from_db()
        source_b.refresh_from_db()
        self.assertEqual(source_a.shared_offer_id, source_b.shared_offer_id)
        product_a.refresh_from_db()
        product_b.refresh_from_db()
        self.assertEqual(product_a.baseline_total_cents, 87260)
        self.assertEqual(product_b.baseline_total_cents, 88260)
        self.assertEqual(PriceObservation.objects.filter(is_baseline=True).count(), 2)
        self.assertEqual(
            PriceObservation.objects.get(tenant=tenant_a).shared_observation_id,
            PriceObservation.objects.get(tenant=tenant_b).shared_observation_id,
        )

    def test_validation_failure_for_one_tenant_does_not_poison_shared_base(self):
        _tenant_a, product_a, _source_a = self._tenant_source("A")
        tenant_b, product_b, _source_b = self._tenant_source(
            "B",
            model="WRONG-MODEL",
            postal_code="20040002",
        )

        result = collect_active_offers(
            fetcher=lambda _url: AMAZON_HTML,
            shipping_quoter=lambda *_args, **_kwargs: ShippingQuote(
                price=Decimal("10.00"),
                delivery_min_days=2,
                delivery_max_days=4,
            ),
            rate_limiter=no_wait_limiter(),
        )

        self.assertEqual(result.status, CollectionBatchStatus.PARTIAL)
        self.assertEqual(SharedOfferObservation.objects.get().status, "OK")
        self.assertEqual(PriceObservation.objects.get(tenant=tenant_b).status, "MODEL_MISMATCH")
        self.assertEqual(CollectorError.objects.filter(tenant=tenant_b).count(), 1)
        product_a.refresh_from_db()
        product_b.refresh_from_db()
        self.assertEqual(product_a.baseline_total_cents, 87260)
        self.assertIsNone(product_b.baseline_total_cents)

    def test_kabum_source_uses_structured_parser_and_private_shipping(self):
        tenant = Tenant.objects.create(name="Tenant KaBuM")
        DeliveryProfile.objects.create(tenant=tenant, postal_code="01001000")
        product = MonitoredProduct.objects.create(
            tenant=tenant,
            name="SSD KaBuM",
            exact_model="CT1000BX500SSD1",
            variant="1 TB / SATA",
        )
        OfferSource.objects.create(
            tenant=tenant,
            product=product,
            store="KaBuM",
            url="https://www.kabum.com.br/produto/167492/ssd-crucial",
            expected_seller="TEITEC INFORMÁTICA",
            expected_variant="1 TB / SATA",
        )

        result = collect_active_offers(
            fetcher=lambda _url: KABUM_HTML,
            shipping_quoter=lambda *_args, **_kwargs: ShippingQuote(
                price=Decimal("15.90"),
                delivery_min_days=None,
                delivery_max_days=5,
            ),
            rate_limiter=no_wait_limiter(),
        )

        self.assertEqual(result.status, CollectionBatchStatus.SUCCESS)
        base = SharedOfferObservation.objects.get()
        self.assertEqual(base.product_price_cents, 99900)
        self.assertEqual(base.seller, "TEITEC INFORMÁTICA")
        observation = PriceObservation.objects.get(tenant=tenant)
        self.assertEqual(observation.status, "OK")
        self.assertEqual(observation.total_price_cents, 101490)

    def test_fetch_failure_is_shared_but_recorded_per_tenant(self):
        tenant_a, _product_a, _source_a = self._tenant_source("A")
        tenant_b, _product_b, _source_b = self._tenant_source("B")
        fetcher = Mock(side_effect=ProductPreviewError("FETCH_FAILED", "falha segura"))

        result = collect_active_offers(
            fetcher=fetcher,
            rate_limiter=no_wait_limiter(),
        )

        self.assertEqual(result.urls_fetched, 1)
        fetcher.assert_called_once()
        self.assertEqual(SharedOfferObservation.objects.get().status, "FETCH_ERROR")
        self.assertEqual(
            set(PriceObservation.objects.values_list("status", flat=True)),
            {"FETCH_ERROR"},
        )
        self.assertEqual(CollectorError.objects.filter(tenant=tenant_a).count(), 1)
        self.assertEqual(CollectorError.objects.filter(tenant=tenant_b).count(), 1)

    def test_out_of_stock_does_not_create_baseline_or_collector_error(self):
        tenant, product, _source = self._tenant_source("A")

        result = collect_active_offers(
            fetcher=lambda _url: AMAZON_OUT_OF_STOCK_HTML,
            rate_limiter=no_wait_limiter(),
        )

        self.assertEqual(result.status, CollectionBatchStatus.SUCCESS)
        self.assertEqual(PriceObservation.objects.get(tenant=tenant).status, "OUT_OF_STOCK")
        self.assertEqual(CollectorError.objects.count(), 0)
        product.refresh_from_db()
        self.assertIsNone(product.baseline_total_cents)

    def test_missing_delivery_profile_fails_closed_without_shipping(self):
        tenant, product, _source = self._tenant_source("A", postal_code=None)
        shipping_quoter = Mock()

        result = collect_active_offers(
            fetcher=lambda _url: AMAZON_HTML,
            shipping_quoter=shipping_quoter,
            rate_limiter=no_wait_limiter(),
        )

        self.assertEqual(result.status, CollectionBatchStatus.PARTIAL)
        self.assertEqual(
            PriceObservation.objects.get(tenant=tenant).status,
            "PROFILE_INCOMPLETE",
        )
        shipping_quoter.assert_not_called()
        product.refresh_from_db()
        self.assertIsNone(product.baseline_total_cents)

    def test_pix_only_price_is_not_used_for_card_product(self):
        tenant, product, _source = self._tenant_source(
            "A",
            payment_method="CARD",
        )
        shipping_quoter = Mock()

        result = collect_active_offers(
            fetcher=lambda _url: AMAZON_HTML,
            shipping_quoter=shipping_quoter,
            rate_limiter=no_wait_limiter(),
        )

        self.assertEqual(result.status, CollectionBatchStatus.PARTIAL)
        self.assertEqual(
            PriceObservation.objects.get(tenant=tenant).status,
            "PAYMENT_MISMATCH",
        )
        shipping_quoter.assert_not_called()
        product.refresh_from_db()
        self.assertIsNone(product.baseline_total_cents)

    def test_invalid_shipping_quote_is_not_saved_as_a_total(self):
        tenant, product, _source = self._tenant_source("A")

        result = collect_active_offers(
            fetcher=lambda _url: AMAZON_HTML,
            shipping_quoter=lambda *_args, **_kwargs: ShippingQuote(
                price=Decimal("-1.00"),
                delivery_min_days=4,
                delivery_max_days=2,
            ),
            rate_limiter=no_wait_limiter(),
        )

        self.assertEqual(result.status, CollectionBatchStatus.PARTIAL)
        observation = PriceObservation.objects.get(tenant=tenant)
        self.assertEqual(observation.status, "SHIPPING_UNAVAILABLE")
        self.assertIsNone(observation.shipping_price_cents)
        self.assertIsNone(observation.total_price_cents)
        product.refresh_from_db()
        self.assertIsNone(product.baseline_total_cents)

    def test_baseline_is_created_only_once(self):
        _tenant, product, _source = self._tenant_source("A")
        shipping_quoter = lambda *_args, **_kwargs: ShippingQuote(
            price=Decimal("10.00"),
            delivery_min_days=2,
            delivery_max_days=4,
        )
        first = collect_active_offers(
            fetcher=lambda _url: AMAZON_HTML,
            shipping_quoter=shipping_quoter,
            rate_limiter=no_wait_limiter(),
        )
        second = collect_active_offers(
            fetcher=lambda _url: AMAZON_HTML,
            shipping_quoter=shipping_quoter,
            rate_limiter=no_wait_limiter(),
        )

        self.assertEqual(first.baselines_created, 1)
        self.assertEqual(second.baselines_created, 0)
        self.assertEqual(PriceObservation.objects.filter(is_baseline=True).count(), 1)
        product.refresh_from_db()
        self.assertEqual(product.baseline_total_cents, 87260)

    def test_second_collection_creates_one_alert_event(self):
        _tenant, product, _source = self._tenant_source("A")
        shipping_quoter = lambda *_args, **_kwargs: ShippingQuote(
            price=Decimal("10.00"),
            delivery_min_days=2,
            delivery_max_days=4,
        )

        first = collect_active_offers(
            fetcher=lambda _url: AMAZON_HTML,
            shipping_quoter=shipping_quoter,
            rate_limiter=no_wait_limiter(),
        )
        lower_price_html = AMAZON_HTML.replace("R$ 862,60", "R$ 842,60")
        second = collect_active_offers(
            fetcher=lambda _url: lower_price_html,
            shipping_quoter=shipping_quoter,
            rate_limiter=no_wait_limiter(),
        )

        self.assertEqual(first.alert_events_created, 0)
        self.assertEqual(second.alert_events_created, 1)
        self.assertEqual(AlertEvent.objects.filter(product=product).count(), 1)
        self.assertIn("PRICE_DROP", AlertEvent.objects.get(product=product).event_type)

    def test_invalid_legacy_url_never_reaches_network(self):
        tenant, _product, source = self._tenant_source("A")
        OfferSource.objects.filter(pk=source.pk).update(
            url="https://www.amazon.com.br.attacker.invalid/dp/B07YD579WM"
        )
        fetcher = Mock()

        result = collect_active_offers(
            fetcher=fetcher,
            rate_limiter=no_wait_limiter(),
        )

        self.assertEqual(result.urls_fetched, 0)
        fetcher.assert_not_called()
        self.assertEqual(PriceObservation.objects.get(tenant=tenant).status, "INVALID_URL")

    def test_unexpected_failure_closes_batch_and_tenant_run(self):
        self._tenant_source("A")

        with self.assertRaises(CollectionExecutionError) as raised:
            collect_active_offers(
                fetcher=Mock(side_effect=RuntimeError("boom")),
                rate_limiter=no_wait_limiter(),
            )

        batch = CollectionBatch.objects.get()
        self.assertEqual(raised.exception.batch_id, str(batch.pk))
        self.assertEqual(batch.status, CollectionBatchStatus.FAILED)
        self.assertEqual(batch.error_code, "UNEXPECTED_ERROR")
        self.assertIsNotNone(batch.finished_at)
        run = MonitoringRun.objects.get()
        self.assertEqual(run.status, "FAILED")
        self.assertEqual(run.error_message, "Falha inesperada durante a coleta.")


class StoreRateLimiterTest(TestCase):
    def test_rate_limit_is_independent_per_store(self):
        now = [100.0]
        sleeps: list[float] = []

        def clock():
            return now[0]

        def sleeper(seconds):
            sleeps.append(seconds)
            now[0] += seconds

        limiter = StoreRateLimiter(12, clock=clock, sleeper=sleeper)
        limiter.wait("Amazon")
        limiter.wait("KaBuM")
        limiter.wait("Amazon")

        self.assertEqual(sleeps, [5.0])


class CollectionCommandTest(TestCase):
    def test_command_completes_without_active_sources(self):
        output = StringIO()

        call_command("collect_active_offers", stdout=output)

        self.assertIn("URLs 0/0", output.getvalue())
        self.assertIn("baselines 0", output.getvalue())
