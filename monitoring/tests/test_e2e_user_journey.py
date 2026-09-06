from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from app.shipping import ShippingQuote
from monitoring.collection_engine import StoreRateLimiter, collect_active_offers
from monitoring.models import AlertEvent, MonitoredProduct, NotificationDelivery, ProductLinkPreview
from telegram_bots.delivery import deliver_alert_event
from telegram_bots.models import ManagedBot, ManagedBotStatus
from tenancy.models import Tenant, User


FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"
AMAZON_HTML = (FIXTURES / "amazon" / "product_in_stock.html").read_text(encoding="utf-8")


class FakeTelegramClient:
    def __init__(self):
        self.messages: list[tuple[int, str, dict | None]] = []

    def send_message(self, chat_id, text, *, reply_markup=None):
        self.messages.append((chat_id, text, reply_markup))
        return {"message_id": 9001}


class ControlledEndToEndJourneyTest(TestCase):
    def test_user_configures_collects_and_receives_alert_without_external_network(self):
        tenant = Tenant.objects.create(name="Radar E2E")
        user = User.objects.create_user(username="e2e-user", tenant=tenant)
        self.client.force_login(user)

        settings_response = self.client.post(
            reverse("delivery-settings"),
            {
                "postal_code": "01001-000",
                "preferred_payment_method": "PIX",
                "minimum_price_drop_percent": "1.00",
                "alert_price_increase": "",
            },
        )
        self.assertEqual(settings_response.status_code, 302)

        with patch("monitoring.preview.fetch_preview_html", return_value=AMAZON_HTML) as fetcher:
            preview_response = self.client.post(
                reverse("product-new"),
                {"url": "https://www.amazon.com.br/gp/product/B07YD579WM?ref_=stage10"},
            )
        self.assertEqual(preview_response.status_code, 302)
        self.assertEqual(fetcher.call_count, 1)
        preview = ProductLinkPreview.objects.get()

        confirmation = self.client.post(
            reverse("product-preview-confirm", kwargs={"preview_id": preview.pk}),
            {
                "name": "SSD Crucial BX500 1 TB",
                "exact_model": "CT1000BX500SSD1",
                "variant": "1 TB / SATA",
                "expected_seller": "BPS Oficial",
                "preferred_payment_method": "PIX",
                "target_price": "850,00",
            },
        )
        self.assertEqual(confirmation.status_code, 302)
        product = MonitoredProduct.objects.get()

        shipping = lambda *_args, **_kwargs: ShippingQuote(
            price=Decimal("10.00"),
            delivery_min_days=2,
            delivery_max_days=4,
        )
        limiter = StoreRateLimiter(1_000_000, sleeper=lambda _seconds: None)
        baseline = collect_active_offers(
            fetcher=lambda _url: AMAZON_HTML,
            shipping_quoter=shipping,
            rate_limiter=limiter,
        )
        discounted_html = AMAZON_HTML.replace("R$ 862,60", "R$ 842,60")
        changed = collect_active_offers(
            fetcher=lambda _url: discounted_html,
            shipping_quoter=shipping,
            rate_limiter=limiter,
        )
        self.assertEqual(baseline.baselines_created, 1)
        self.assertEqual(changed.alert_events_created, 1)

        ManagedBot.objects.create(
            tenant=tenant,
            telegram_bot_id=777,
            owner_telegram_user_id=888,
            username="e2e_personal_bot",
            display_name="E2E Personal",
            status=ManagedBotStatus.ACTIVE,
            token_ciphertext="synthetic-ciphertext",
            token_key_version=1,
            chat_id=888,
        )
        fake_telegram = FakeTelegramClient()
        delivery = deliver_alert_event(AlertEvent.objects.get(), client=fake_telegram)

        detail = self.client.get(
            reverse("product-detail-page", kwargs={"product_id": product.pk})
        )
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Preço por dia")
        self.assertContains(detail, "Enviado")
        self.assertEqual(delivery.status, "SENT")
        self.assertEqual(NotificationDelivery.objects.get().message_id, 9001)
        self.assertEqual(len(fake_telegram.messages), 1)
