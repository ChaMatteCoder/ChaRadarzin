from __future__ import annotations

from unittest import skipUnless
from unittest.mock import Mock, patch

from django.db import connection
from django.http import HttpResponseRedirect
from django.test import TestCase, override_settings
from django.urls import reverse

from tenancy.models import BetaAccessGrant, DeliveryProfile, TelegramIdentity, Tenant, User
from monitoring.models import MonitoredProduct
from tenancy.services import (
    TelegramClaims,
    TelegramIdentityError,
    login_or_create_telegram_user,
)


class TelegramClaimsTest(TestCase):
    def test_keeps_oidc_subject_separate_from_telegram_user_id(self) -> None:
        claims = TelegramClaims.from_mapping(
            {
                "iss": "https://oauth.telegram.org",
                "sub": "oidc-subject-abc",
                "id": "922337203685477000",
                "name": "Pessoa Teste",
                "preferred_username": "@pessoa_teste",
            }
        )

        self.assertEqual(claims.subject, "oidc-subject-abc")
        self.assertEqual(claims.telegram_user_id, 922337203685477000)
        self.assertEqual(claims.username, "pessoa_teste")

    def test_rejects_unexpected_issuer(self) -> None:
        with self.assertRaises(TelegramIdentityError):
            TelegramClaims.from_mapping(
                {"iss": "https://example.invalid", "sub": "one", "id": "123"}
            )


@override_settings(BETA_ACCESS_REQUIRED=False)
class TelegramIdentityServiceTest(TestCase):
    def setUp(self) -> None:
        self.claims = TelegramClaims(
            issuer="https://oauth.telegram.org",
            subject="subject-one",
            telegram_user_id=1234567890123,
            display_name="Ana Teste",
            username="ana_teste",
        )

    def test_creates_one_tenant_and_one_unusable_password_user(self) -> None:
        user = login_or_create_telegram_user(self.claims)

        self.assertIsNotNone(user.tenant_id)
        self.assertFalse(user.has_usable_password())
        self.assertEqual(Tenant.objects.count(), 1)
        self.assertEqual(TelegramIdentity.objects.count(), 1)

    def test_is_idempotent_and_updates_safe_profile_claims(self) -> None:
        first_user = login_or_create_telegram_user(self.claims)
        second_user = login_or_create_telegram_user(
            TelegramClaims(
                issuer=self.claims.issuer,
                subject=self.claims.subject,
                telegram_user_id=self.claims.telegram_user_id,
                display_name="Ana Atualizada",
                username="ana_nova",
            )
        )

        self.assertEqual(first_user.pk, second_user.pk)
        self.assertEqual(Tenant.objects.count(), 1)
        identity = TelegramIdentity.objects.get()
        self.assertEqual(identity.display_name, "Ana Atualizada")

    @skipUnless(connection.vendor == "postgresql", "Regressao especifica do PostgreSQL")
    def test_postgresql_locks_only_identity_with_nullable_related_tenant(self) -> None:
        first_user = login_or_create_telegram_user(self.claims)

        second_user = login_or_create_telegram_user(self.claims)

        self.assertEqual(second_user.pk, first_user.pk)
        self.assertEqual(TelegramIdentity.objects.count(), 1)

    def test_rejects_subject_rebound_to_another_telegram_id(self) -> None:
        login_or_create_telegram_user(self.claims)

        with self.assertRaises(TelegramIdentityError):
            login_or_create_telegram_user(
                TelegramClaims(
                    issuer=self.claims.issuer,
                    subject=self.claims.subject,
                    telegram_user_id=999,
                    display_name="Conflito",
                )
            )

    def test_rejects_telegram_id_rebound_to_another_subject(self) -> None:
        login_or_create_telegram_user(self.claims)

        with self.assertRaises(TelegramIdentityError):
            login_or_create_telegram_user(
                TelegramClaims(
                    issuer=self.claims.issuer,
                    subject="another-subject",
                    telegram_user_id=self.claims.telegram_user_id,
                    display_name="Conflito",
                )
            )


@override_settings(BETA_ACCESS_REQUIRED=False, SECURE_SSL_REDIRECT=False)
class TelegramOidcViewTest(TestCase):
    @override_settings(TELEGRAM_OIDC_ENABLED=False)
    def test_disabled_login_does_not_start_external_flow(self) -> None:
        response = self.client.get(reverse("telegram-login"))

        self.assertEqual(response.status_code, 403)

    @override_settings(TELEGRAM_OIDC_ENABLED=True)
    @patch("tenancy.views.get_telegram_client")
    def test_login_keeps_only_a_safe_local_next_url(self, get_client: Mock) -> None:
        get_client.return_value.authorize_redirect.return_value = HttpResponseRedirect(
            "https://oauth.telegram.org/auth"
        )

        response = self.client.get(
            reverse("telegram-login"),
            {"next": "https://attacker.invalid/steal"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertNotIn("oidc_next", self.client.session)

        self.client.get(reverse("telegram-login"), {"next": reverse("dashboard")})
        self.assertEqual(self.client.session["oidc_next"], reverse("dashboard"))

    @override_settings(TELEGRAM_OIDC_ENABLED=True)
    @patch("tenancy.views.get_telegram_client")
    def test_callback_creates_authenticated_session(self, get_client: Mock) -> None:
        get_client.return_value.authorize_access_token.return_value = {
            "userinfo": {
                "iss": "https://oauth.telegram.org",
                "sub": "subject-from-callback",
                "id": "7654321",
                "name": "Conta Callback",
            }
        }

        response = self.client.get(reverse("telegram-callback"))

        self.assertRedirects(response, reverse("dashboard"), fetch_redirect_response=False)
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(int(self.client.session["_auth_user_id"]), User.objects.get().pk)


@override_settings(BETA_ACCESS_REQUIRED=False, SECURE_SSL_REDIRECT=False)
class PrivacyViewTest(TestCase):
    def setUp(self) -> None:
        self.user = login_or_create_telegram_user(
            TelegramClaims(
                issuer="https://oauth.telegram.org",
                subject="privacy-subject",
                telegram_user_id=998877,
                display_name="Conta Privacidade",
                username="privacidade",
            )
        )
        DeliveryProfile.objects.create(tenant=self.user.tenant, postal_code="01001000")
        MonitoredProduct.objects.create(tenant=self.user.tenant, name="Produto privado")
        BetaAccessGrant.objects.create(
            telegram_user_id=998877,
            redeemed_by=self.user,
        )
        self.client.force_login(self.user)

    def test_privacy_policy_is_public(self) -> None:
        self.client.logout()
        response = self.client.get(reverse("privacy-policy"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dados utilizados")

    def test_account_deletion_rejects_wrong_confirmation(self) -> None:
        response = self.client.post(reverse("delete-account"), {"confirmation": "EXCLUIR"})
        self.assertEqual(response.status_code, 400)
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())

    @patch("tenancy.privacy.disconnect_remote_bot_best_effort", return_value=True)
    def test_account_deletion_erases_single_user_tenant(self, disconnect: Mock) -> None:
        tenant_id = self.user.tenant_id
        response = self.client.post(
            reverse("delete-account"),
            {"confirmation": "EXCLUIR MINHA CONTA"},
        )
        self.assertRedirects(response, reverse("landing"), fetch_redirect_response=False)
        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())
        self.assertFalse(Tenant.objects.filter(pk=tenant_id).exists())
        self.assertFalse(TelegramIdentity.objects.filter(telegram_user_id=998877).exists())
        self.assertFalse(BetaAccessGrant.objects.filter(telegram_user_id=998877).exists())
        self.assertEqual(MonitoredProduct.objects.count(), 0)
        disconnect.assert_not_called()
