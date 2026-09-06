from __future__ import annotations

from cryptography.fernet import Fernet
from django.test import TestCase, override_settings
from django.urls import reverse

from telegram_bots.crypto import BotTokenCipher, SecretDecryptionError
from telegram_bots.models import BotOnboardingSession, ManagedBot
from telegram_bots.onboarding import (
    OnboardingError,
    build_managed_bot_deep_link,
    start_onboarding,
    validate_bot_username,
)
from tenancy.models import TelegramIdentity, Tenant, User


@override_settings(
    BOT_TOKEN_ENCRYPTION_KEYS=f"1:{Fernet.generate_key().decode('ascii')}",
    TELEGRAM_MANAGER_BOT_USERNAME="chadarad_manager_bot",
)
class BotCryptoOnboardingTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Radar teste")
        self.user = User.objects.create_user(username="ana", tenant=self.tenant)
        TelegramIdentity.objects.create(
            user=self.user,
            issuer="https://oauth.telegram.org",
            subject="oidc-ana",
            telegram_user_id=111222333,
            display_name="Ana Teste",
        )

    def test_ciphertext_does_not_contain_token_and_round_trips(self):
        token = "123456:secret-token-value"
        cipher = BotTokenCipher.from_settings()
        encrypted = cipher.encrypt(token)
        self.assertNotIn(token, encrypted.ciphertext)
        self.assertEqual(cipher.decrypt(encrypted.ciphertext, encrypted.key_version), token)

    def test_unknown_key_version_is_safe(self):
        with self.assertRaises(SecretDecryptionError):
            BotTokenCipher.from_settings().decrypt("not-a-token", 99)

    def test_onboarding_is_idempotent_and_deep_link_is_official_shape(self):
        first = start_onboarding(self.user)
        second = start_onboarding(self.user)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(BotOnboardingSession.objects.count(), 1)
        link = build_managed_bot_deep_link(first)
        self.assertTrue(link.startswith("https://t.me/newbot/chadarad_manager_bot/"))
        self.assertIn("name=", link)

    def test_username_validation_rejects_non_bot(self):
        with self.assertRaises(OnboardingError):
            validate_bot_username("not-valid")

    def test_status_requires_login(self):
        response = self.client.get(reverse("bot-status"))
        self.assertEqual(response.status_code, 302)
