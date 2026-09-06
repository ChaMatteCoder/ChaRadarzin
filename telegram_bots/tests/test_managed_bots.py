from __future__ import annotations

from cryptography.fernet import Fernet
from django.test import TestCase, override_settings

from telegram_bots.configuration import (
    process_managed_bot_update,
    rotate_managed_bot_token,
)
from telegram_bots.crypto import BotTokenCipher
from telegram_bots.models import (
    BotOnboardingSession,
    ManagedBot,
    ManagedBotStatus,
    OnboardingStatus,
)
from telegram_bots.onboarding import start_onboarding
from tenancy.models import TelegramIdentity, Tenant, User


class FakeManager:
    def __init__(self):
        self.restricted = []
        self.tokens = {7654321: "managed-token-synthetic"}

    def get_managed_bot_token(self, bot_id):
        return self.tokens[bot_id]

    def replace_managed_bot_token(self, bot_id):
        self.tokens[bot_id] = "rotated-token-synthetic"
        return self.tokens[bot_id]

    def restrict_managed_bot_to_owner(self, bot_id):
        self.restricted.append(bot_id)
        return True


class FakeChild:
    def __init__(self, bot_id=7654321):
        self.bot_id = bot_id
        self.webhooks = []
        self.commands = []

    def get_me(self):
        return {"id": self.bot_id, "is_bot": True, "username": "owner_radar_bot", "first_name": "Owner Radar"}

    def set_my_commands(self, commands):
        self.commands.append(commands)
        return True

    def set_webhook(self, url, secret_token, *, allowed_updates, drop_pending_updates=False):
        self.webhooks.append((url, secret_token, allowed_updates))
        return True


@override_settings(
    BOT_TOKEN_ENCRYPTION_KEYS=f"1:{Fernet.generate_key().decode('ascii')}",
    TELEGRAM_MANAGER_ENABLED=True,
    TELEGRAM_MANAGER_BOT_USERNAME="manager_bot",
    PUBLIC_BASE_URL="https://radar.example",
)
class ManagedBotFlowTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Radar manager")
        self.user = User.objects.create_user(username="owner", tenant=self.tenant)
        TelegramIdentity.objects.create(
            user=self.user,
            issuer="https://oauth.telegram.org",
            subject="managed-owner",
            telegram_user_id=12345,
            display_name="Owner",
        )
        self.session = start_onboarding(self.user)
        self.manager = FakeManager()
        self.child = FakeChild()

    def test_managed_update_associates_user_restricts_access_and_configures(self):
        result = process_managed_bot_update(
            {
                "update_id": 1,
                "managed_bot": {
                    "user": {"id": 12345},
                    "bot": {"id": 7654321, "username": "owner_radar_bot", "first_name": "Owner Radar"},
                },
            },
            manager_client=self.manager,
            child_client=self.child,
        )
        bot = ManagedBot.objects.get()
        self.assertEqual(result.outcome_code, "AWAITING_START")
        self.assertEqual(bot.owner_telegram_user_id, 12345)
        self.assertEqual(bot.status, ManagedBotStatus.AWAITING_START)
        self.assertTrue(bot.has_token)
        self.assertEqual(self.manager.restricted, [7654321])
        self.assertEqual(len(self.child.webhooks), 1)
        self.assertEqual(
            BotTokenCipher.from_settings().decrypt(bot.token_ciphertext, bot.token_key_version),
            "managed-token-synthetic",
        )
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, OnboardingStatus.AWAITING_START)

    def test_rotation_replaces_encrypted_token(self):
        bot = process_managed_bot_update(
            {
                "update_id": 2,
                "managed_bot": {
                    "user": {"id": 12345},
                    "bot": {"id": 7654321, "username": "owner_radar_bot"},
                },
            },
            manager_client=self.manager,
            child_client=self.child,
        ).bot
        old_ciphertext = bot.token_ciphertext
        rotate_managed_bot_token(bot, manager_client=self.manager, child_client=self.child)
        bot.refresh_from_db()
        self.assertNotEqual(bot.token_ciphertext, old_ciphertext)
        self.assertEqual(
            BotTokenCipher.from_settings().decrypt(bot.token_ciphertext, bot.token_key_version),
            "rotated-token-synthetic",
        )

    def test_unassociated_bot_is_not_created(self):
        result = process_managed_bot_update(
            {
                "update_id": 3,
                "managed_bot": {"user": {"id": 999}, "bot": {"id": 7654321}},
            },
            manager_client=self.manager,
            child_client=self.child,
        )
        self.assertEqual(result.outcome_code, "UNASSOCIATED_OWNER")
        self.assertEqual(ManagedBot.objects.count(), 0)
