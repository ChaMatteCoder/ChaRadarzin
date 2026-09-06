from __future__ import annotations

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from monitoring.models import MonitoredProduct, OfferSource
from tenancy.models import Tenant, User


class TenantIsolationTest(TestCase):
    def setUp(self) -> None:
        self.tenant_a = Tenant.objects.create(name="Radar A")
        self.tenant_b = Tenant.objects.create(name="Radar B")
        self.user_a = User.objects.create_user(
            username="user_a",
            password="test-only-password-a",
            tenant=self.tenant_a,
        )
        self.user_b = User.objects.create_user(
            username="user_b",
            password="test-only-password-b",
            tenant=self.tenant_b,
        )
        self.product_a = MonitoredProduct.objects.create(
            tenant=self.tenant_a,
            name="Produto exclusivo A",
        )
        self.product_b = MonitoredProduct.objects.create(
            tenant=self.tenant_b,
            name="Produto secreto B",
        )

    def test_dashboard_only_lists_current_tenant_products(self) -> None:
        self.client.force_login(self.user_a)

        response = self.client.get(reverse("dashboard"))

        self.assertContains(response, "Produto exclusivo A")
        self.assertNotContains(response, "Produto secreto B")

    def test_api_list_only_returns_current_tenant_products(self) -> None:
        self.client.force_login(self.user_a)

        response = self.client.get(reverse("product-list"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        self.assertEqual(response.json()[0]["name"], "Produto exclusivo A")

    def test_unauthenticated_api_request_is_rejected(self) -> None:
        response = self.client.get(reverse("product-list"))

        self.assertEqual(response.status_code, 403)

    def test_api_detail_hides_another_tenant_object_as_not_found(self) -> None:
        self.client.force_login(self.user_a)

        response = self.client.get(
            reverse("product-detail", kwargs={"pk": self.product_b.pk})
        )

        self.assertEqual(response.status_code, 404)

    def test_api_is_read_only_in_foundation_stage(self) -> None:
        self.client.force_login(self.user_a)

        response = self.client.post(
            reverse("product-list"),
            {"name": "Tentativa"},
        )

        self.assertEqual(response.status_code, 405)

    def test_cross_tenant_relation_fails_model_validation(self) -> None:
        source = OfferSource(
            tenant=self.tenant_a,
            product=self.product_b,
            store="Loja",
            url="https://example.com/product",
        )

        with self.assertRaises(ValidationError):
            source.full_clean()

    def test_tenant_queryset_requires_an_explicit_tenant(self) -> None:
        self.assertEqual(MonitoredProduct.objects.for_tenant(None).count(), 0)
        self.assertEqual(
            list(MonitoredProduct.objects.for_tenant(self.tenant_a)),
            [self.product_a],
        )
