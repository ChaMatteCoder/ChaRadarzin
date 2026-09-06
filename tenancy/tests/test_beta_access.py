from __future__ import annotations

from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings

from tenancy.models import BetaAccessGrant, TelegramIdentity
from tenancy.services import (
    TelegramClaims,
    TelegramIdentityError,
    login_or_create_telegram_user,
)


def claims(telegram_user_id: int = 123456) -> TelegramClaims:
    return TelegramClaims(
        issuer="https://oauth.telegram.org",
        subject=f"subject-{telegram_user_id}",
        telegram_user_id=telegram_user_id,
        display_name="Pessoa Beta",
        username="pessoa_beta",
    )


@override_settings(BETA_ACCESS_REQUIRED=True)
class BetaAccessTest(TestCase):
    def test_unregistered_identity_cannot_create_account(self):
        with self.assertRaisesRegex(TelegramIdentityError, "beta fechado"):
            login_or_create_telegram_user(claims())

        self.assertFalse(TelegramIdentity.objects.exists())

    def test_active_grant_creates_identity_and_records_redemption(self):
        grant = BetaAccessGrant.objects.create(telegram_user_id=123456)

        user = login_or_create_telegram_user(claims())

        grant.refresh_from_db()
        self.assertEqual(grant.redeemed_by, user)
        self.assertIsNotNone(grant.redeemed_at)
        self.assertEqual(user.telegram_identity.telegram_user_id, 123456)

    def test_disabled_grant_blocks_login(self):
        BetaAccessGrant.objects.create(telegram_user_id=123456, active=False)

        with self.assertRaises(TelegramIdentityError):
            login_or_create_telegram_user(claims())

    def test_registration_command_is_idempotent_and_can_disable(self):
        output = StringIO()
        call_command(
            "register_beta_user",
            "--telegram-user-id",
            "123456",
            "--username",
            "@pessoa_beta",
            stdout=output,
        )
        call_command(
            "register_beta_user",
            "--telegram-user-id",
            "123456",
            "--disable",
            stdout=output,
        )

        grant = BetaAccessGrant.objects.get()
        self.assertFalse(grant.active)
        self.assertEqual(grant.telegram_username, "pessoa_beta")
        self.assertNotIn("123456", output.getvalue())
