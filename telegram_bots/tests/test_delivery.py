from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock

from cryptography.fernet import Fernet
from django.test import TestCase, override_settings
from django.utils import timezone

from monitoring.job_queue import process_next_job
from monitoring.models import (
    AlertEvent,
    CollectionBatch,
    CollectionJobStatus,
    CollectionJobType,
    MonitoredProduct,
    MonitoringRun,
    NotificationDelivery,
    NotificationDeliveryStatus,
    OfferSource,
    PriceObservation,
    RunStatus,
)
from telegram_bots.client import TelegramApiError, TelegramTransportError
from telegram_bots.crypto import BotTokenCipher
from telegram_bots.delivery import (
    deliver_alert_event,
    deliver_pending_alerts,
    send_test_notification,
)
from telegram_bots.models import (
    ManagedBot,
    ManagedBotStatus,
    ManualRefreshRequest,
    ManualRefreshStatus,
)
from telegram_bots.updates import process_personal_bot_update
from tenancy.models import Tenant


class FakeTelegramClient:
    def __init__(self, response=None, error=None):
        self.response = response or {"message_id": 77}
        self.error = error
        self.sent = []
        self.callbacks = []

    def send_message(self, chat_id, text, *, reply_markup=None):
        if self.error:
            raise self.error
        self.sent.append((chat_id, text, reply_markup))
        return self.response

    def answer_callback_query(self, callback_query_id, text=""):
        self.callbacks.append((callback_query_id, text))
        return True


@override_settings(
    BOT_TOKEN_ENCRYPTION_KEYS=f"1:{Fernet.generate_key().decode('ascii')}",
    PUBLIC_BASE_URL="http://127.0.0.1:8000",
    TELEGRAM_DELIVERY_LEASE_SECONDS=300,
)
class AlertDeliveryTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant entrega")
        self.product = MonitoredProduct.objects.create(
            tenant=self.tenant,
            name="SSD <teste> & 1 TB",
            target_price_cents=95000,
        )
        self.source = OfferSource.objects.create(
            tenant=self.tenant,
            product=self.product,
            store="Amazon",
            url="https://www.amazon.com.br/dp/B07YD579WM",
        )
        batch = CollectionBatch.objects.create(
            started_at=timezone.now() - timedelta(minutes=1),
        )
        run = MonitoringRun.objects.create(
            tenant=self.tenant,
            collection_batch=batch,
            started_at=batch.started_at,
            status=RunStatus.SUCCESS,
            mode="SHARED_COLLECTION",
            input_file_name="test",
        )
        self.observation = PriceObservation.objects.create(
            tenant=self.tenant,
            run=run,
            product=self.product,
            source=self.source,
            observed_at=timezone.now(),
            seller="Loja <oficial>",
            payment_method="PIX",
            product_price_cents=90000,
            shipping_price_cents=0,
            total_price_cents=90000,
            in_stock=True,
            status="OK",
            parser_version="test",
        )
        self.event = AlertEvent.objects.create(
            tenant=self.tenant,
            product=self.product,
            observation=self.observation,
            event_type="PRICE_DROP|TARGET_REACHED",
            idempotency_key="delivery-event-1",
            occurred_at=self.observation.observed_at,
            current_price_cents=90000,
            previous_price_cents=100000,
            historical_low_cents=100000,
            target_price_cents=95000,
            variation_percent=Decimal("-10.000"),
            reason="Preço caiu e atingiu o preço-alvo.",
        )
        cipher = BotTokenCipher.from_settings()
        encrypted = cipher.encrypt("123456:ABCDEF")
        self.bot = ManagedBot.objects.create(
            tenant=self.tenant,
            telegram_bot_id=555,
            owner_telegram_user_id=999,
            username="chadar_test_bot",
            display_name="ChaRadar Test",
            status=ManagedBotStatus.ACTIVE,
            token_ciphertext=encrypted.ciphertext,
            token_key_version=encrypted.key_version,
            chat_id=999,
        )

    def test_delivery_is_formatted_and_idempotent(self):
        client = FakeTelegramClient()

        first = deliver_alert_event(self.event, client=client)
        second = deliver_alert_event(self.event, client=client)

        self.assertEqual(first.status, "SENT")
        self.assertEqual(second.status, "SENT")
        self.assertEqual(first.delivery_id, second.delivery_id)
        self.assertEqual(len(client.sent), 1)
        delivery = NotificationDelivery.objects.get(alert_event=self.event)
        self.assertEqual(delivery.status, NotificationDeliveryStatus.SENT)
        self.assertEqual(delivery.message_id, 77)
        text = client.sent[0][1]
        self.assertIn("SSD &lt;teste&gt; &amp; 1 TB", text)
        markup = client.sent[0][2]
        callback = markup["inline_keyboard"][-1][0]["callback_data"]
        self.assertEqual(callback, f"pause_product:{self.product.pk}")
        self.assertIn("https://www.amazon.com.br/dp/B07YD579WM", markup["inline_keyboard"][0][0]["url"])

    def test_admin_test_notification_does_not_create_alert_history(self):
        client = FakeTelegramClient()
        alerts_before = AlertEvent.objects.count()
        deliveries_before = NotificationDelivery.objects.count()

        result = send_test_notification(self.bot, client=client)

        self.assertEqual(result.status, "SENT")
        self.assertEqual(AlertEvent.objects.count(), alerts_before)
        self.assertEqual(NotificationDelivery.objects.count(), deliveries_before)
        text = client.sent[0][1]
        self.assertIn("notificação operacional de teste", text)
        self.assertIn("Nenhum preço, alerta ou histórico foi alterado", text)
        markup = client.sent[0][2]
        self.assertEqual(
            markup["inline_keyboard"][0][0],
            {"text": "Abrir painel", "url": "http://127.0.0.1:8000/painel/"},
        )

    def test_missing_connection_is_blocked_without_external_call(self):
        self.bot.status = ManagedBotStatus.AWAITING_START
        self.bot.save(update_fields=["status", "updated_at"])
        client = Mock()

        result = deliver_alert_event(self.event, client=client)

        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_code, "BOT_NOT_CONNECTED")
        client.send_message.assert_not_called()
        self.assertEqual(
            NotificationDelivery.objects.get(alert_event=self.event).status,
            NotificationDeliveryStatus.BLOCKED,
        )

    def test_api_unauthorized_revokes_bot_and_records_failure(self):
        client = FakeTelegramClient(error=TelegramApiError("sendMessage", 401))

        result = deliver_alert_event(self.event, client=client)

        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.error_code, "BOT_REVOKED")
        self.bot.refresh_from_db()
        self.assertEqual(self.bot.status, ManagedBotStatus.REVOKED)
        self.assertFalse(self.bot.has_token)
        delivery = NotificationDelivery.objects.get(alert_event=self.event)
        self.assertEqual(delivery.status, NotificationDeliveryStatus.FAILED)

    def test_api_forbidden_marks_bot_blocked(self):
        client = FakeTelegramClient(error=TelegramApiError("sendMessage", 403))

        result = deliver_alert_event(self.event, client=client)

        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.error_code, "BOT_BLOCKED")
        self.bot.refresh_from_db()
        self.assertEqual(self.bot.status, ManagedBotStatus.BLOCKED)

    def test_transport_failure_can_be_retried_and_keeps_safe_error(self):
        failing = FakeTelegramClient(error=TelegramTransportError("sendMessage"))
        first = deliver_alert_event(self.event, client=failing)
        self.assertEqual(first.status, "FAILED")
        delivery = NotificationDelivery.objects.get(alert_event=self.event)
        self.assertEqual(delivery.error_code, "TELEGRAM_TRANSPORT_sendMessage")

        success = FakeTelegramClient()
        second = deliver_alert_event(self.event, client=success)
        self.assertEqual(second.status, "SENT")
        self.assertEqual(len(success.sent), 1)

    def test_invalid_encrypted_token_is_recorded_without_leaking_secret(self):
        self.bot.token_ciphertext = "not-a-valid-token"
        self.bot.save(update_fields=["token_ciphertext", "updated_at"])

        result = deliver_alert_event(self.event)

        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.error_code, "TOKEN_DECRYPTION_FAILED")
        self.bot.refresh_from_db()
        self.assertEqual(self.bot.status, ManagedBotStatus.ERROR)
        delivery = NotificationDelivery.objects.get(alert_event=self.event)
        self.assertEqual(delivery.error_message, "Falha segura ao entregar o alerta.")

    def test_pending_delivery_isolated_by_tenant(self):
        other_tenant = Tenant.objects.create(name="Outro tenant")
        other_product = MonitoredProduct.objects.create(tenant=other_tenant, name="Outro")
        other_source = OfferSource.objects.create(
            tenant=other_tenant,
            product=other_product,
            store="Amazon",
            url="https://www.amazon.com.br/dp/B08N5KWB9H",
        )
        batch = CollectionBatch.objects.create(started_at=timezone.now())
        run = MonitoringRun.objects.create(
            tenant=other_tenant,
            collection_batch=batch,
            started_at=batch.started_at,
            status=RunStatus.SUCCESS,
            mode="SHARED_COLLECTION",
            input_file_name="test",
        )
        observation = PriceObservation.objects.create(
            tenant=other_tenant,
            run=run,
            product=other_product,
            source=other_source,
            observed_at=timezone.now(),
            seller="Loja",
            payment_method="PIX",
            product_price_cents=100,
            shipping_price_cents=0,
            total_price_cents=100,
            in_stock=True,
            status="OK",
            parser_version="test",
        )
        other_event = AlertEvent.objects.create(
            tenant=other_tenant,
            product=other_product,
            observation=observation,
            event_type="PRICE_DROP",
            idempotency_key="delivery-event-2",
            occurred_at=observation.observed_at,
            current_price_cents=100,
            reason="Queda",
        )
        cipher = BotTokenCipher.from_settings()
        encrypted = cipher.encrypt("123456:OTHER")
        other_bot = ManagedBot.objects.create(
            tenant=other_tenant,
            telegram_bot_id=556,
            owner_telegram_user_id=1000,
            username="other_delivery_bot",
            display_name="Other delivery",
            status=ManagedBotStatus.ACTIVE,
            token_ciphertext=encrypted.ciphertext,
            token_key_version=encrypted.key_version,
            chat_id=1000,
        )
        other_client = FakeTelegramClient()
        clients = {self.tenant.pk: FakeTelegramClient(), other_tenant.pk: other_client}

        results = deliver_pending_alerts(
            client_factory=lambda bot: clients[bot.tenant_id],
        )

        self.assertEqual({result.status for result in results}, {"SENT"})
        self.assertEqual(len(clients[self.tenant.pk].sent), 1)
        self.assertEqual(len(other_client.sent), 1)
        self.assertIn("SSD &lt;teste&gt;", clients[self.tenant.pk].sent[0][1])
        self.assertIn("Outro", other_client.sent[0][1])
        self.assertNotIn("Outro", clients[self.tenant.pk].sent[0][1])
        self.assertEqual(other_event.tenant_id, other_tenant.pk)
        self.assertEqual(other_bot.chat_id, 1000)

    def test_pause_callback_is_owner_scoped(self):
        client = FakeTelegramClient()
        update = {
            "callback_query": {
                "id": "callback-1",
                "from": {"id": 999},
                "data": f"pause_product:{self.product.pk}",
                "message": {"chat": {"id": 999, "type": "private"}},
            }
        }

        outcome = process_personal_bot_update(self.bot, update, client=client)

        self.assertEqual(outcome, "PRODUCT_PAUSED")
        self.product.refresh_from_db()
        self.assertEqual(self.product.status, "PAUSED")
        self.assertIn("pausados", client.callbacks[0][1])

        self.product.status = "ACTIVE"
        self.product.save(update_fields=["status", "updated_at"])
        update["callback_query"]["from"]["id"] = 1000
        update["callback_query"]["message"]["chat"]["id"] = 1000
        self.assertEqual(
            process_personal_bot_update(self.bot, update, client=client),
            "NON_OWNER_REJECTED",
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.status, "ACTIVE")


@override_settings(
    BOT_TOKEN_ENCRYPTION_KEYS=f"1:{Fernet.generate_key().decode('ascii')}",
)
class ManualRefreshQueueTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant refresh")
        self.bot = ManagedBot.objects.create(
            tenant=self.tenant,
            telegram_bot_id=556,
            owner_telegram_user_id=1001,
            username="refresh_test_bot",
            display_name="Refresh",
            status=ManagedBotStatus.ACTIVE,
            chat_id=1001,
        )

    def test_refresh_creates_manual_job_and_worker_completes_request(self):
        client = FakeTelegramClient()
        update = {
            "message": {
                "from": {"id": 1001},
                "chat": {"id": 1001, "type": "private"},
                "text": "/atualizar",
            }
        }
        self.assertEqual(process_personal_bot_update(self.bot, update, client=client), "REFRESH_REQUESTED")
        request = ManualRefreshRequest.objects.get()
        self.assertEqual(request.collection_job.job_type, CollectionJobType.MANUAL_COLLECTION)
        self.assertEqual(request.status, ManualRefreshStatus.PENDING)

        batch = CollectionBatch.objects.create(started_at=timezone.now())
        from monitoring.collection_engine import CollectionResult

        result = CollectionResult(
            batch_id=str(batch.pk),
            status="SUCCESS",
            urls_planned=0,
            urls_fetched=0,
            sources_processed=0,
            tenant_runs_count=0,
            baselines_created=0,
        )
        worker = process_next_job(collector=lambda: result, worker_id="manual-test")
        self.assertEqual(worker.state, "SUCCESS")
        request.refresh_from_db()
        self.assertEqual(request.status, ManualRefreshStatus.COMPLETED)
        self.assertIsNotNone(request.finished_at)
