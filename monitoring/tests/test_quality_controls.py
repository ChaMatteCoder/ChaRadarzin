from __future__ import annotations

import json
import logging
from datetime import timedelta
from io import StringIO

from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from chadaradzin.logging import JsonFormatter
from chadaradzin.rate_limit import consume_rate_limit
from monitoring.models import CollectionBatch, CollectionBatchStatus, CollectionJob
from monitoring.operations import build_operational_snapshot


class JsonLoggingTest(SimpleTestCase):
    def test_formatter_emits_json_and_ignores_sensitive_extras(self):
        record = logging.LogRecord(
            name="monitoring.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="collection_finished",
            args=(),
            exc_info=None,
        )
        record.event_code = "COLLECTION_FINISHED"
        record.duration_ms = 125
        record.chat_id = 123456789
        record.postal_code = "01001000"

        payload = json.loads(JsonFormatter().format(record))

        self.assertEqual(payload["event"], "collection_finished")
        self.assertEqual(payload["event_code"], "COLLECTION_FINISHED")
        self.assertEqual(payload["duration_ms"], 125)
        self.assertNotIn("chat_id", payload)
        self.assertNotIn("postal_code", payload)


class FixedWindowRateLimitTest(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_blocks_after_limit_and_resets_in_next_window(self):
        first = consume_rate_limit("test", "same-client", limit=2, window_seconds=60, now=120)
        second = consume_rate_limit("test", "same-client", limit=2, window_seconds=60, now=121)
        blocked = consume_rate_limit("test", "same-client", limit=2, window_seconds=60, now=122)
        reset = consume_rate_limit("test", "same-client", limit=2, window_seconds=60, now=180)

        self.assertTrue(first.allowed)
        self.assertTrue(second.allowed)
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.remaining, 0)
        self.assertTrue(reset.allowed)

    def test_keeps_buckets_and_identities_independent(self):
        consume_rate_limit("one", "client-a", limit=1, window_seconds=60, now=120)

        self.assertTrue(
            consume_rate_limit("one", "client-b", limit=1, window_seconds=60, now=120).allowed
        )
        self.assertTrue(
            consume_rate_limit("two", "client-a", limit=1, window_seconds=60, now=120).allowed
        )


@override_settings(OPERATIONAL_LOOKBACK_HOURS=24, OPERATIONAL_COLLECTION_STALE_HOURS=30)
class OperationalSnapshotTest(TestCase):
    def test_clean_recent_collection_reports_ok(self):
        now = timezone.now()
        CollectionBatch.objects.create(
            started_at=now - timedelta(minutes=2),
            finished_at=now - timedelta(minutes=1),
            status=CollectionBatchStatus.SUCCESS,
        )

        snapshot = build_operational_snapshot(now=now)

        self.assertEqual(snapshot.status, "ok")
        self.assertEqual(snapshot.alerts, ())
        self.assertIsNotNone(snapshot.metrics["collection_last_success_at"])

    def test_partial_collection_counts_as_completion_and_warns(self):
        now = timezone.now()
        CollectionBatch.objects.create(
            started_at=now - timedelta(minutes=2),
            finished_at=now - timedelta(minutes=1),
            status=CollectionBatchStatus.PARTIAL,
        )

        snapshot = build_operational_snapshot(now=now)

        self.assertEqual(snapshot.status, "warning")
        self.assertIsNotNone(snapshot.metrics["collection_last_success_at"])
        self.assertEqual(snapshot.metrics["collection_partial_recent"], 1)
        self.assertIn("PARTIAL_COLLECTION_BATCHES", {alert.code for alert in snapshot.alerts})

    def test_dead_job_is_a_critical_operational_alert(self):
        now = timezone.now()
        CollectionBatch.objects.create(
            started_at=now - timedelta(minutes=2),
            finished_at=now - timedelta(minutes=1),
            status=CollectionBatchStatus.SUCCESS,
        )
        CollectionJob.objects.create(
            idempotency_key="dead-stage-10",
            status="DEAD",
            scheduled_for=now,
            available_at=now,
            max_attempts=1,
            attempt_count=1,
        )

        snapshot = build_operational_snapshot(now=now)

        self.assertEqual(snapshot.status, "critical")
        self.assertIn("DEAD_COLLECTION_JOBS", {alert.code for alert in snapshot.alerts})

    def test_command_emits_machine_readable_metrics_and_can_fail_closed(self):
        output = StringIO()
        call_command("operational_status", "--json", stdout=output)

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["status"], "warning")
        self.assertNotIn("postal_code", output.getvalue())
        self.assertNotIn("token", output.getvalue().casefold())
        with self.assertRaises(CommandError):
            call_command("operational_status", "--fail-on", "warning", stdout=StringIO())
