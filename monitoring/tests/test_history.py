from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from cryptography.fernet import Fernet
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from monitoring.history import build_product_history
from monitoring.models import (
    AlertEvent,
    CollectionBatch,
    MonitoredProduct,
    MonitoringRun,
    NotificationDelivery,
    OfferSource,
    PriceObservation,
    RunStatus,
)
from telegram_bots.crypto import BotTokenCipher
from telegram_bots.models import ManagedBot
from tenancy.models import Tenant, User


def at(day: int) -> datetime:
    return timezone.make_aware(datetime(2026, 8, day, 10))


@override_settings(
    BOT_TOKEN_ENCRYPTION_KEYS=f"1:{Fernet.generate_key().decode('ascii')}",
)
class ProductHistoryTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Histórico")
        self.user = User.objects.create_user(username="history-user", tenant=self.tenant)
        self.product = MonitoredProduct.objects.create(
            tenant=self.tenant,
            name="SSD Histórico",
            target_price_cents=90000,
        )
        self.source_a = OfferSource.objects.create(
            tenant=self.tenant,
            product=self.product,
            store="Amazon",
            url="https://www.amazon.com.br/dp/B07YD579WM",
        )
        self.source_b = OfferSource.objects.create(
            tenant=self.tenant,
            product=self.product,
            store="KaBuM",
            url="https://www.kabum.com.br/produto/167492/ssd",
        )

    def observation(self, source, day: int, total: int) -> PriceObservation:
        batch = CollectionBatch.objects.create(started_at=at(day))
        run = MonitoringRun.objects.create(
            tenant=self.tenant,
            collection_batch=batch,
            started_at=at(day),
            status=RunStatus.SUCCESS,
            mode="SHARED_COLLECTION",
            input_file_name="history-test",
        )
        return PriceObservation.objects.create(
            tenant=self.tenant,
            run=run,
            product=self.product,
            source=source,
            observed_at=at(day),
            seller="Loja",
            payment_method="PIX",
            product_price_cents=total,
            shipping_price_cents=0,
            total_price_cents=total,
            in_stock=True,
            status="OK",
            parser_version="test",
        )

    def test_summary_selects_best_current_offer_and_daily_history(self):
        self.observation(self.source_a, 1, 100000)
        self.observation(self.source_a, 3, 90000)
        self.observation(self.source_b, 2, 88000)

        summary = build_product_history(self.product, sources=[self.source_a, self.source_b])

        self.assertEqual(summary.best_offer.source_id, self.source_b.pk)
        self.assertEqual(summary.best_offer.total_price_cents, 88000)
        self.assertEqual(summary.historical_low_cents, 88000)
        self.assertEqual(summary.previous_best_cents, 100000)
        self.assertEqual(summary.variation_percent, Decimal("-12.0"))
        self.assertEqual([row.day.day for row in summary.rows], [3, 2, 1])
        self.assertEqual(len(summary.points), 3)
        self.assertIn("8.0,", summary.chart_points)

    def test_detail_explains_history_events_and_bot_state(self):
        first = self.observation(self.source_a, 1, 100000)
        current = self.observation(self.source_a, 3, 90000)
        event = AlertEvent.objects.create(
            tenant=self.tenant,
            product=self.product,
            observation=current,
            event_type="PRICE_DROP",
            idempotency_key="history-event",
            occurred_at=current.observed_at,
            current_price_cents=90000,
            previous_price_cents=100000,
            historical_low_cents=100000,
            reason="Queda relevante de preço.",
        )
        NotificationDelivery.objects.create(
            tenant=self.tenant,
            run=current.run,
            product=self.product,
            alert_event=event,
            delivery_key="history-delivery",
            attempted_at=current.observed_at,
            channel="TELEGRAM_PERSONAL",
            event_type=event.event_type,
            price_cents=90000,
            previous_price_cents=100000,
            status="SENT",
            message_id=123,
        )
        cipher = BotTokenCipher.from_settings()
        encrypted = cipher.encrypt("123456:history")
        ManagedBot.objects.create(
            tenant=self.tenant,
            telegram_bot_id=789,
            owner_telegram_user_id=456,
            username="history_bot",
            display_name="History bot",
            status="ACTIVE",
            token_ciphertext=encrypted.ciphertext,
            token_key_version=encrypted.key_version,
            chat_id=456,
        )
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("product-detail-page", kwargs={"product_id": self.product.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Melhor oferta")
        self.assertContains(response, "Menor histórico")
        self.assertContains(response, "R$ 900,00")
        self.assertContains(response, "-10,0%")
        self.assertContains(response, "history-chart")
        self.assertContains(response, "Eventos enviados")
        self.assertContains(response, "Enviado")
        self.assertContains(response, "Conectado")

    def test_detail_has_clear_empty_state_before_first_valid_price(self):
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("product-detail-page", kwargs={"product_id": self.product.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ainda não há preço comparável")
        self.assertContains(response, "Histórico começa na primeira coleta")
        self.assertContains(response, "Nenhum evento relevante")

    def test_dashboard_shows_current_offer_when_history_exists(self):
        self.observation(self.source_a, 1, 100000)
        self.observation(self.source_b, 2, 88000)
        self.client.force_login(self.user)

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "R$ 880,00")
        self.assertContains(response, "melhor total")

    def test_latest_out_of_stock_does_not_show_a_stale_current_price(self):
        self.observation(self.source_a, 1, 100000)
        batch = CollectionBatch.objects.create(started_at=at(2))
        run = MonitoringRun.objects.create(
            tenant=self.tenant,
            collection_batch=batch,
            started_at=at(2),
            status=RunStatus.SUCCESS,
            mode="SHARED_COLLECTION",
            input_file_name="history-test",
        )
        PriceObservation.objects.create(
            tenant=self.tenant,
            run=run,
            product=self.product,
            source=self.source_a,
            observed_at=at(2),
            seller="Loja",
            payment_method="PIX",
            product_price_cents=None,
            shipping_price_cents=None,
            total_price_cents=None,
            in_stock=False,
            status="OUT_OF_STOCK",
            parser_version="test",
        )

        summary = build_product_history(self.product, sources=[self.source_a])

        self.assertIsNone(summary.best_offer)
        self.assertEqual(summary.historical_low_cents, 100000)

    def test_failed_run_is_not_displayed_as_price_history(self):
        successful = self.observation(self.source_a, 1, 100000)
        failed = self.observation(self.source_a, 2, 50000)
        MonitoringRun.objects.filter(pk=failed.run_id).update(status=RunStatus.FAILED)

        summary = build_product_history(self.product, sources=[self.source_a])

        self.assertEqual(summary.best_offer, successful)
        self.assertEqual(summary.historical_low_cents, 100000)
        self.assertEqual([row.total_price_cents for row in summary.rows], [100000])
