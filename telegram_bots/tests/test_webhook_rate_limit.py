from __future__ import annotations

import json

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from telegram_bots.crypto import webhook_secret_digest
from telegram_bots.models import ManagedBot
from tenancy.models import Tenant


@override_settings(
    TELEGRAM_WEBHOOK_RATE_LIMIT=1,
    TELEGRAM_WEBHOOK_RATE_WINDOW_SECONDS=60,
)
class WebhookRateLimitTest(TestCase):
    def setUp(self):
        cache.clear()
        tenant = Tenant.objects.create(name="Rate limit")
        self.bot = ManagedBot.objects.create(
            tenant=tenant,
            telegram_bot_id=111,
            owner_telegram_user_id=222,
            username="rate_limit_bot",
            display_name="Rate Limit Bot",
            webhook_secret_digest=webhook_secret_digest("expected-secret"),
        )

    def tearDown(self):
        cache.clear()

    def _post(self):
        return self.client.post(
            reverse("personal-bot-webhook", kwargs={"webhook_id": self.bot.webhook_public_id}),
            data=json.dumps({"update_id": 1, "message": {}}),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="wrong-secret",
        )

    def test_rate_limit_protects_before_repeated_secret_validation(self):
        first = self._post()
        second = self._post()

        self.assertEqual(first.status_code, 403)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.headers["X-RateLimit-Remaining"], "0")
        self.assertGreaterEqual(int(second.headers["Retry-After"]), 1)
