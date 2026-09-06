from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from monitoring.models import (
    MonitoredProduct,
    OfferSource,
    ProductLinkPreview,
    ProductPreviewStatus,
    ProductStatus,
)
from monitoring.preview import (
    ProductPreviewError,
    confirm_product_link_preview,
    create_product_link_preview,
)
from tenancy.models import DeliveryProfile, Tenant, User


FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"
AMAZON_HTML = (FIXTURES / "amazon" / "product_in_stock.html").read_text(encoding="utf-8")
KABUM_HTML = (FIXTURES / "kabum" / "product_in_stock.html").read_text(encoding="utf-8")


class ProductPreviewServiceTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Preview Tenant")
        self.user = User.objects.create_user(username="preview-user", tenant=self.tenant)

    def test_preview_and_confirmation_create_active_product_atomically(self):
        preview = create_product_link_preview(
            self.user,
            "https://www.amazon.com.br/gp/product/B07YD579WM?ref_=tracking",
            fetcher=lambda _url: AMAZON_HTML,
        )
        self.assertEqual(preview.status, ProductPreviewStatus.READY)
        self.assertEqual(preview.extracted_seller, "BPS Oficial")
        self.assertEqual(preview.extracted_price_cents, 86260)

        product, source = confirm_product_link_preview(
            preview,
            name="SSD Crucial BX500",
            exact_model="CT1000BX500SSD1",
            variant="1 TB / SATA",
            expected_seller="BPS Oficial",
            preferred_payment_method="PIX",
            target_price_cents=85000,
        )

        self.assertEqual(product.status, ProductStatus.ACTIVE)
        self.assertEqual(product.target_price_cents, 85000)
        self.assertEqual(source.url, "https://www.amazon.com.br/dp/B07YD579WM")
        preview.refresh_from_db()
        self.assertEqual(preview.status, ProductPreviewStatus.CONFIRMED)
        self.assertEqual(preview.confirmed_source, source)

    def test_confirmation_is_idempotent(self):
        preview = create_product_link_preview(
            self.user,
            "https://www.kabum.com.br/produto/167492/ssd",
            fetcher=lambda _url: KABUM_HTML,
        )
        kwargs = {
            "name": "SSD",
            "exact_model": "CT1000BX500SSD1",
            "variant": "1 TB",
            "expected_seller": "TEITEC INFORMÁTICA",
            "preferred_payment_method": "PIX",
            "target_price_cents": None,
        }
        first = confirm_product_link_preview(preview, **kwargs)
        second = confirm_product_link_preview(preview, **kwargs)
        self.assertEqual(first[0].pk, second[0].pk)
        self.assertEqual(MonitoredProduct.objects.count(), 1)
        self.assertEqual(OfferSource.objects.count(), 1)

    def test_active_source_still_blocks_duplicate_url(self):
        product = MonitoredProduct.objects.create(
            tenant=self.tenant,
            name="Produto existente",
            exact_model="CT1000BX500SSD1",
            variant="1 TB",
        )
        OfferSource.objects.create(
            tenant=self.tenant,
            product=product,
            store="Amazon",
            url="https://www.amazon.com.br/dp/B07YD579WM",
            expected_seller="BPS Oficial",
            expected_variant="1 TB",
            active=True,
        )

        with self.assertRaises(ProductPreviewError) as context:
            create_product_link_preview(
                self.user,
                "https://www.amazon.com.br/dp/B07YD579WM?th",
                fetcher=lambda _url: AMAZON_HTML,
            )

        self.assertEqual(context.exception.code, "DUPLICATE_URL")

    def test_paused_source_can_be_reused_by_another_product(self):
        old_product = MonitoredProduct.objects.create(
            tenant=self.tenant,
            name="Produto incorreto",
            exact_model="OUTRO-MODELO",
            variant="32 polegadas",
        )
        paused_source = OfferSource.objects.create(
            tenant=self.tenant,
            product=old_product,
            store="Amazon",
            url="https://www.amazon.com.br/dp/B07YD579WM",
            expected_seller="BPS Oficial",
            expected_variant="1 TB",
            active=False,
        )
        target_product = MonitoredProduct.objects.create(
            tenant=self.tenant,
            name="SSD Crucial BX500",
            exact_model="CT1000BX500SSD1",
            variant="1 TB",
        )

        preview = create_product_link_preview(
            self.user,
            "https://www.amazon.com.br/dp/B07YD579WM?th",
            target_product=target_product,
            fetcher=lambda _url: AMAZON_HTML,
        )
        _product, new_source = confirm_product_link_preview(
            preview,
            name=target_product.name,
            exact_model=target_product.exact_model,
            variant=target_product.variant,
            expected_seller="BPS Oficial",
            preferred_payment_method="PIX",
            target_price_cents=None,
        )

        paused_source.refresh_from_db()
        self.assertFalse(paused_source.active)
        self.assertEqual(paused_source.product, old_product)
        self.assertTrue(new_source.active)
        self.assertEqual(new_source.product, target_product)
        self.assertEqual(new_source.url, paused_source.url)
        self.assertNotEqual(new_source.pk, paused_source.pk)

    @override_settings(PRODUCT_PREVIEW_RATE_LIMIT=2, PRODUCT_PREVIEW_WINDOW_MINUTES=10)
    def test_preview_rate_limit_is_persistent_per_tenant(self):
        create_product_link_preview(
            self.user,
            "https://www.amazon.com.br/dp/B07YD579WM",
            fetcher=lambda _url: AMAZON_HTML,
        )
        create_product_link_preview(
            self.user,
            "https://www.amazon.com.br/dp/B000000001",
            fetcher=lambda _url: AMAZON_HTML,
        )
        with self.assertRaises(ProductPreviewError) as context:
            create_product_link_preview(
                self.user,
                "https://www.amazon.com.br/dp/B000000002",
                fetcher=lambda _url: AMAZON_HTML,
            )
        self.assertEqual(context.exception.code, "PREVIEW_RATE_LIMIT")

    def test_expired_preview_cannot_create_product(self):
        preview = create_product_link_preview(
            self.user,
            "https://www.amazon.com.br/dp/B07YD579WM",
            fetcher=lambda _url: AMAZON_HTML,
        )
        ProductLinkPreview.objects.filter(pk=preview.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        preview.refresh_from_db()
        with self.assertRaises(ProductPreviewError) as context:
            confirm_product_link_preview(
                preview,
                name="SSD",
                exact_model="CT1000BX500SSD1",
                variant="1 TB",
                expected_seller="BPS Oficial",
                preferred_payment_method="PIX",
                target_price_cents=None,
            )
        self.assertEqual(context.exception.code, "PREVIEW_EXPIRED")
        self.assertEqual(MonitoredProduct.objects.count(), 0)
        preview.refresh_from_db()
        self.assertEqual(preview.status, ProductPreviewStatus.EXPIRED)


class ProductPanelFlowTest(TestCase):
    def setUp(self):
        self.tenant_a = Tenant.objects.create(name="Radar A")
        self.tenant_b = Tenant.objects.create(name="Radar B")
        self.user_a = User.objects.create_user(username="panel-a", tenant=self.tenant_a)
        self.user_b = User.objects.create_user(username="panel-b", tenant=self.tenant_b)
        self.client.force_login(self.user_a)

    @patch("monitoring.preview.fetch_preview_html", return_value=AMAZON_HTML)
    def test_user_previews_confirms_and_activates_product_from_site(self, fetcher):
        response = self.client.post(
            reverse("product-new"),
            {"url": "https://www.amazon.com.br/dp/B07YD579WM?tag=tracking"},
        )
        self.assertEqual(response.status_code, 302)
        preview = ProductLinkPreview.objects.get()
        self.assertRedirects(
            response,
            reverse("product-preview-confirm", kwargs={"preview_id": preview.pk}),
            fetch_redirect_response=False,
        )
        confirmation = self.client.get(response.url)
        self.assertContains(confirmation, "SSD Crucial BX500")
        self.assertContains(confirmation, "BPS Oficial")

        response = self.client.post(
            response.url,
            {
                "name": "SSD Crucial BX500 1 TB",
                "exact_model": "CT1000BX500SSD1",
                "variant": "1 TB / SATA",
                "expected_seller": "BPS Oficial",
                "preferred_payment_method": "PIX",
                "target_price": "850,00",
            },
        )
        product = MonitoredProduct.objects.get()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(product.status, ProductStatus.ACTIVE)
        self.assertEqual(product.target_price_cents, 85000)
        self.assertEqual(product.sources.count(), 1)
        dashboard = self.client.get(reverse("dashboard"))
        self.assertContains(dashboard, "Aguardando primeira coleta")
        self.assertEqual(fetcher.call_count, 1)

    def test_malicious_url_is_rejected_before_any_preview(self):
        with patch("monitoring.preview.fetch_preview_html") as fetcher:
            response = self.client.post(
                reverse("product-new"),
                {"url": "https://www.amazon.com.br.attacker.invalid/dp/B07YD579WM"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Amazon Brasil ou da KaBuM")
        self.assertEqual(ProductLinkPreview.objects.count(), 0)
        fetcher.assert_not_called()

    def test_delivery_profile_keeps_only_needed_fields(self):
        response = self.client.post(
            reverse("delivery-settings"),
            {
                "postal_code": "01001-000",
                "preferred_payment_method": "CARD",
                "minimum_price_drop_percent": "1.50",
                "alert_price_increase": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        profile = DeliveryProfile.objects.get(tenant=self.tenant_a)
        self.assertEqual(profile.postal_code, "01001000")
        self.assertEqual(profile.preferred_payment_method, "CARD")
        self.assertFalse(hasattr(profile, "street"))

    @patch("monitoring.preview.fetch_preview_html", return_value=KABUM_HTML)
    def test_existing_product_can_receive_another_exact_link(self, _fetcher):
        product = MonitoredProduct.objects.create(
            tenant=self.tenant_a,
            name="SSD Crucial",
            exact_model="CT1000BX500SSD1",
            variant="1 TB / SATA",
        )
        OfferSource.objects.create(
            tenant=self.tenant_a,
            product=product,
            store="Amazon",
            url="https://www.amazon.com.br/dp/B07YD579WM",
            expected_seller="BPS Oficial",
            expected_variant="1 TB / SATA",
        )
        response = self.client.post(
            reverse("source-new", kwargs={"product_id": product.pk}),
            {"url": "https://www.kabum.com.br/produto/167492/ssd"},
        )
        preview = ProductLinkPreview.objects.get()
        self.assertEqual(preview.target_product, product)
        response = self.client.post(
            reverse("product-preview-confirm", kwargs={"preview_id": preview.pk}),
            {
                "name": product.name,
                "exact_model": product.exact_model,
                "variant": product.variant,
                "expected_seller": "TEITEC INFORMÁTICA",
                "preferred_payment_method": "PIX",
                "target_price": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(MonitoredProduct.objects.count(), 1)
        self.assertEqual(OfferSource.objects.filter(product=product).count(), 2)

    def test_pause_archive_and_cross_tenant_isolation(self):
        product_b = MonitoredProduct.objects.create(
            tenant=self.tenant_b,
            name="Segredo B",
            exact_model="SECRET-B",
            variant="B",
        )
        for route_name in ("product-detail-page", "product-edit", "product-toggle-pause", "product-archive"):
            method = self.client.post if route_name in {"product-toggle-pause", "product-archive"} else self.client.get
            response = method(reverse(route_name, kwargs={"product_id": product_b.pk}))
            self.assertEqual(response.status_code, 404)

        product_a = MonitoredProduct.objects.create(
            tenant=self.tenant_a,
            name="Produto A",
            exact_model="MODEL-A",
            variant="A",
        )
        self.client.post(reverse("product-toggle-pause", kwargs={"product_id": product_a.pk}))
        product_a.refresh_from_db()
        self.assertEqual(product_a.status, ProductStatus.PAUSED)
        self.client.post(reverse("product-archive", kwargs={"product_id": product_a.pk}))
        product_a.refresh_from_db()
        self.assertEqual(product_a.status, ProductStatus.ARCHIVED)
        self.assertNotContains(self.client.get(reverse("dashboard")), "Produto A")
