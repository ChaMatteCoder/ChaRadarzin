from __future__ import annotations

from cryptography.fernet import Fernet
from django.test import TestCase, override_settings
from django.urls import reverse

from telegram_bots.crypto import webhook_secret_digest
from telegram_bots.models import (
    BotSecurityEvent,
    ManagedBot,
    ManagedBotStatus,
    ManualRefreshRequest,
    TelegramUpdateReceipt,
    UpdateProcessingStatus,
)
from telegram_bots.updates import process_personal_bot_update
from tenancy.models import TelegramIdentity, Tenant, User


class FakeTelegramClient:
    def __init__(self):
        self.sent = []
        self.callbacks = []

    def send_message(self, chat_id, text, *, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))
        return {"message_id": 1}

    def answer_callback_query(self, callback_query_id, text=""):
        self.callbacks.append((callback_query_id, text))
        return True


@override_settings(BOT_TOKEN_ENCRYPTION_KEYS=f"1:{Fernet.generate_key().decode('ascii')}")
class PersonalWebhookTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Radar webhook")
        self.user = User.objects.create_user(username="dono", tenant=self.tenant)
        TelegramIdentity.objects.create(
            user=self.user,
            issuer="https://oauth.telegram.org",
            subject="oidc-owner",
            telegram_user_id=987654321,
            display_name="Dono",
        )
        self.bot = ManagedBot.objects.create(
            tenant=self.tenant,
            telegram_bot_id=7654321,
            owner_telegram_user_id=987654321,
            username="radar_owner_bot",
            display_name="Radar Owner",
            status=ManagedBotStatus.AWAITING_START,
            webhook_secret_digest=webhook_secret_digest("webhook-secret"),
        )
        self.fake = FakeTelegramClient()

    def post_update(self, update, secret="webhook-secret"):
        return self.client.post(
            reverse("personal-bot-webhook", kwargs={"webhook_id": self.bot.webhook_public_id}),
            data=__import__("json").dumps(update),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=secret,
        )

    def test_wrong_secret_is_rejected(self):
        response = self.post_update({"update_id": 1, "message": {}} , secret="wrong")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(TelegramUpdateReceipt.objects.count(), 0)

    def test_owner_start_activates_and_duplicate_is_idempotent(self):
        update = {
            "update_id": 42,
            "message": {
                "from": {"id": 987654321},
                "chat": {"id": 987654321, "type": "private"},
                "text": "/start",
            },
        }
        from unittest.mock import patch

        with patch("telegram_bots.updates._client_for", return_value=self.fake):
            first = self.post_update(update)
            second = self.post_update(update)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.bot.refresh_from_db()
        self.assertEqual(self.bot.status, ManagedBotStatus.ACTIVE)
        self.assertEqual(self.bot.chat_id, 987654321)
        self.assertEqual(len(self.fake.sent), 1)
        receipt = TelegramUpdateReceipt.objects.get()
        self.assertEqual(receipt.status, UpdateProcessingStatus.PROCESSED)

    def test_non_owner_gets_no_message_and_security_event(self):
        from unittest.mock import patch

        update = {
            "update_id": 43,
            "message": {
                "from": {"id": 555},
                "chat": {"id": 555, "type": "private"},
                "text": "/status",
            },
        }
        with patch("telegram_bots.updates._client_for", return_value=self.fake):
            response = self.post_update(update)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.fake.sent, [])
        self.assertTrue(BotSecurityEvent.objects.filter(event_code="NON_OWNER_UPDATE_REJECTED").exists())

    def test_refresh_command_has_cooldown(self):
        from unittest.mock import patch

        update = {
            "update_id": 44,
            "message": {
                "from": {"id": 987654321},
                "chat": {"id": 987654321, "type": "private"},
                "text": "/atualizar",
            },
        }
        with patch("telegram_bots.updates._client_for", return_value=self.fake):
            self.post_update(update)
            update["update_id"] = 45
            self.post_update(update)
        self.assertEqual(ManualRefreshRequest.objects.count(), 1)
        self.assertEqual(len(self.fake.sent), 2)

    def test_block_event_marks_bot_blocked(self):
        update = {
            "update_id": 46,
            "my_chat_member": {
                "chat": {"id": 987654321, "type": "private"},
                "new_chat_member": {"status": "kicked"},
            },
        }
        response = self.post_update(update)
        self.assertEqual(response.status_code, 200)
        self.bot.refresh_from_db()
        self.assertEqual(self.bot.status, ManagedBotStatus.BLOCKED)
