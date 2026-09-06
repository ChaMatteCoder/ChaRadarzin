from __future__ import annotations

from datetime import datetime, timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from monitoring.collection_engine import CollectionResult
from monitoring.job_queue import (
    enqueue_daily_collection,
    process_next_job,
    requeue_dead_job,
)
from monitoring.models import (
    CollectionBatch,
    CollectionBatchStatus,
    CollectionJob,
    CollectionJobAttempt,
    CollectionJobAttemptStatus,
    CollectionJobStatus,
    CollectionJobType,
    CollectionWorkerLease,
)


def local_datetime(year: int, month: int, day: int, hour: int, minute: int):
    return timezone.make_aware(
        datetime(year, month, day, hour, minute),
        timezone.get_current_timezone(),
    )


def queued_job(*, now, max_attempts=4):
    return CollectionJob.objects.create(
        job_type=CollectionJobType.DAILY_COLLECTION,
        idempotency_key=f"test:{now.isoformat()}:{CollectionJob.objects.count()}",
        status=CollectionJobStatus.QUEUED,
        scheduled_for=now,
        available_at=now,
        max_attempts=max_attempts,
    )


class DailySchedulerTest(TestCase):
    def test_scheduler_is_idempotent_for_the_local_day(self):
        now = local_datetime(2026, 8, 29, 21, 6)

        first = enqueue_daily_collection(now=now, daily_at="21:05")
        second = enqueue_daily_collection(now=now, daily_at="21:05")

        self.assertTrue(first.due)
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.job.pk, second.job.pk)
        self.assertEqual(CollectionJob.objects.count(), 1)
        self.assertEqual(first.job.idempotency_key, "daily-collection:2026-08-29")

    def test_scheduler_does_not_enqueue_before_configured_time(self):
        result = enqueue_daily_collection(
            now=local_datetime(2026, 8, 29, 21, 4),
            daily_at="21:05",
        )

        self.assertFalse(result.due)
        self.assertIsNone(result.job)
        self.assertEqual(CollectionJob.objects.count(), 0)


class CollectionWorkerTest(TestCase):
    def test_default_worker_delivers_after_collection(self):
        now = timezone.now()
        queued_job(now=now)
        batch = CollectionBatch.objects.create(
            started_at=now,
            finished_at=now,
            status=CollectionBatchStatus.SUCCESS,
        )
        result = CollectionResult(
            batch_id=str(batch.pk),
            status=CollectionBatchStatus.SUCCESS,
            urls_planned=0,
            urls_fetched=0,
            sources_processed=0,
            tenant_runs_count=0,
            baselines_created=0,
        )

        with (
            patch("monitoring.job_queue.collect_active_offers", return_value=result) as collect,
            patch("telegram_bots.delivery.deliver_pending_alerts") as deliver,
        ):
            worker_result = process_next_job(
                worker_id="delivery-worker",
                wall_clock=lambda: now,
                monotonic_clock=lambda: 0.0,
            )

        self.assertEqual(worker_result.state, "SUCCESS")
        collect.assert_called_once_with()
        deliver.assert_called_once_with()

    def test_success_records_attempt_duration_and_batch(self):
        started_at = timezone.now()
        job = queued_job(now=started_at)
        batch = CollectionBatch.objects.create(
            started_at=started_at,
            finished_at=started_at,
            status=CollectionBatchStatus.SUCCESS,
        )
        result = CollectionResult(
            batch_id=str(batch.pk),
            status=CollectionBatchStatus.SUCCESS,
            urls_planned=0,
            urls_fetched=0,
            sources_processed=0,
            tenant_runs_count=0,
            baselines_created=0,
        )
        wall_times = iter((started_at, started_at + timedelta(seconds=2)))
        monotonic_times = iter((10.0, 12.0))

        worker_result = process_next_job(
            collector=lambda: result,
            worker_id="worker-test",
            wall_clock=lambda: next(wall_times),
            monotonic_clock=lambda: next(monotonic_times),
        )

        self.assertEqual(worker_result.state, "SUCCESS")
        job.refresh_from_db()
        self.assertEqual(job.status, CollectionJobStatus.SUCCESS)
        self.assertEqual(job.attempt_count, 1)
        self.assertEqual(job.duration_ms, 2000)
        self.assertEqual(job.collection_batch, batch)
        attempt = job.attempts.get()
        self.assertEqual(attempt.status, CollectionJobAttemptStatus.SUCCESS)
        self.assertEqual(attempt.duration_ms, 2000)
        self.assertEqual(attempt.collection_batch, batch)
        lease = CollectionWorkerLease.objects.get(key="shared-collection")
        self.assertEqual(lease.owner, "")
        self.assertIsNone(lease.locked_until)

    @override_settings(
        COLLECTION_JOB_MAX_ATTEMPTS=2,
        COLLECTION_RETRY_BASE_SECONDS=60,
        COLLECTION_RETRY_MAX_SECONDS=600,
    )
    def test_retry_uses_backoff_then_moves_to_dead_letter(self):
        started_at = timezone.now()
        job = queued_job(now=started_at, max_attempts=2)

        first_times = iter((started_at, started_at + timedelta(seconds=1)))
        first = process_next_job(
            collector=lambda: (_ for _ in ()).throw(ConnectionError("sensitive")),
            worker_id="worker-one",
            wall_clock=lambda: next(first_times),
            monotonic_clock=iter((1.0, 2.0)).__next__,
        )

        self.assertEqual(first.state, "FAILED")
        self.assertEqual(first.job_status, CollectionJobStatus.RETRY_WAIT)
        job.refresh_from_db()
        self.assertEqual(job.available_at, started_at + timedelta(seconds=61))
        self.assertNotIn("sensitive", job.last_error_message)

        retry_at = job.available_at
        second_times = iter((retry_at, retry_at + timedelta(seconds=2)))
        second = process_next_job(
            collector=lambda: (_ for _ in ()).throw(ConnectionError("secret")),
            worker_id="worker-two",
            wall_clock=lambda: next(second_times),
            monotonic_clock=iter((3.0, 5.0)).__next__,
        )

        self.assertEqual(second.job_status, CollectionJobStatus.DEAD)
        job.refresh_from_db()
        self.assertEqual(job.status, CollectionJobStatus.DEAD)
        self.assertEqual(job.attempt_count, 2)
        self.assertEqual(job.attempts.count(), 2)
        self.assertEqual(
            set(job.attempts.values_list("status", flat=True)),
            {CollectionJobAttemptStatus.FAILED},
        )

    def test_active_lease_prevents_a_second_worker(self):
        now = timezone.now()
        job = queued_job(now=now)
        CollectionWorkerLease.objects.create(
            key="shared-collection",
            owner="existing-worker",
            acquired_at=now,
            locked_until=now + timedelta(minutes=10),
        )

        result = process_next_job(
            collector=lambda: self.fail("collector nao deveria executar"),
            worker_id="second-worker",
            wall_clock=lambda: now,
        )

        self.assertEqual(result.state, "BUSY")
        job.refresh_from_db()
        self.assertEqual(job.status, CollectionJobStatus.QUEUED)
        self.assertEqual(job.attempt_count, 0)

    @override_settings(
        COLLECTION_RETRY_BASE_SECONDS=60,
        COLLECTION_RETRY_MAX_SECONDS=600,
    )
    def test_expired_worker_is_recorded_and_requeued(self):
        now = timezone.now()
        job = queued_job(now=now - timedelta(minutes=20))
        job.status = CollectionJobStatus.RUNNING
        job.attempt_count = 1
        job.claimed_by = "lost-worker"
        job.claimed_at = now - timedelta(minutes=20)
        job.lease_expires_at = now - timedelta(minutes=10)
        job.save()
        CollectionJobAttempt.objects.create(
            job=job,
            number=1,
            worker_id="lost-worker",
            started_at=now - timedelta(minutes=20),
        )
        CollectionWorkerLease.objects.create(
            key="shared-collection",
            owner="lost-worker",
            acquired_at=now - timedelta(minutes=20),
            locked_until=now - timedelta(minutes=10),
        )

        result = process_next_job(
            collector=lambda: self.fail("backoff impede coleta imediata"),
            worker_id="recovery-worker",
            wall_clock=lambda: now,
        )

        self.assertEqual(result.state, "IDLE")
        job.refresh_from_db()
        self.assertEqual(job.status, CollectionJobStatus.RETRY_WAIT)
        self.assertEqual(job.last_error_code, "WORKER_LEASE_EXPIRED")
        attempt = job.attempts.get(number=1)
        self.assertEqual(attempt.status, CollectionJobAttemptStatus.ABANDONED)
        self.assertEqual(attempt.error_code, "WORKER_LEASE_EXPIRED")


class QueueCommandTest(TestCase):
    def test_schedule_command_force_enqueues_job(self):
        output = StringIO()

        call_command("schedule_daily_collection", force=True, stdout=output)

        self.assertEqual(CollectionJob.objects.count(), 1)
        self.assertIn("adicionado a fila", output.getvalue())

    def test_worker_once_exits_cleanly_with_empty_queue(self):
        output = StringIO()

        call_command("run_collection_worker", once=True, stdout=output)

        self.assertIn("Nenhum job disponivel", output.getvalue())

    @override_settings(COLLECTION_JOB_MAX_ATTEMPTS=4)
    def test_dead_job_can_be_requeued_without_losing_attempt_history(self):
        now = timezone.now()
        job = queued_job(now=now, max_attempts=2)
        job.status = CollectionJobStatus.DEAD
        job.attempt_count = 2
        job.finished_at = now
        job.last_error_code = "WORKER_ERROR"
        job.save()

        requeued = requeue_dead_job(job.pk, now=now + timedelta(minutes=1))

        self.assertEqual(requeued.status, CollectionJobStatus.QUEUED)
        self.assertEqual(requeued.attempt_count, 2)
        self.assertEqual(requeued.max_attempts, 6)
        self.assertEqual(requeued.last_error_code, "")
        self.assertIsNone(requeued.finished_at)
