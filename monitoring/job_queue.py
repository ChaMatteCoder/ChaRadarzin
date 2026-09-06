from __future__ import annotations

import os
import logging
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, time as datetime_time, timedelta
from typing import Callable

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from monitoring.collection_engine import (
    CollectionExecutionError,
    CollectionResult,
    collect_active_offers,
)
from monitoring.models import (
    CollectionBatch,
    CollectionJob,
    CollectionJobAttempt,
    CollectionJobAttemptStatus,
    CollectionJobStatus,
    CollectionJobType,
    CollectionWorkerLease,
)


COLLECTION_LEASE_KEY = "shared-collection"
Collector = Callable[[], CollectionResult]
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ScheduleResult:
    job: CollectionJob | None
    created: bool
    due: bool


@dataclass(frozen=True, slots=True)
class WorkerResult:
    state: str
    job_id: str = ""
    job_status: str = ""
    attempt_number: int = 0
    batch_id: str = ""
    error_code: str = ""


def _daily_time(value: str) -> datetime_time:
    hour, minute = (int(part) for part in value.split(":"))
    return datetime_time(hour=hour, minute=minute)


def enqueue_daily_collection(
    *,
    now: datetime | None = None,
    daily_at: str | None = None,
) -> ScheduleResult:
    current = now or timezone.now()
    local_now = timezone.localtime(current)
    configured_time = _daily_time(daily_at or settings.COLLECTION_DAILY_TIME)
    scheduled_local = timezone.make_aware(
        datetime.combine(local_now.date(), configured_time),
        timezone.get_current_timezone(),
    )
    if local_now < scheduled_local:
        return ScheduleResult(job=None, created=False, due=False)

    scheduled_for = scheduled_local.astimezone(timezone.get_current_timezone())
    key = f"daily-collection:{local_now.date().isoformat()}"
    job, created = CollectionJob.objects.get_or_create(
        idempotency_key=key,
        defaults={
            "job_type": CollectionJobType.DAILY_COLLECTION,
            "status": CollectionJobStatus.QUEUED,
            "scheduled_for": scheduled_for,
            "available_at": scheduled_for,
            "max_attempts": settings.COLLECTION_JOB_MAX_ATTEMPTS,
        },
    )
    return ScheduleResult(job=job, created=created, due=True)


def enqueue_collection_now(
    *,
    now: datetime | None = None,
    job_type: CollectionJobType = CollectionJobType.MANUAL_COLLECTION,
) -> CollectionJob:
    current = now or timezone.now()
    return CollectionJob.objects.create(
        job_type=job_type,
        idempotency_key=f"manual-collection:{uuid.uuid4()}",
        status=CollectionJobStatus.QUEUED,
        scheduled_for=current,
        available_at=current,
        max_attempts=settings.COLLECTION_JOB_MAX_ATTEMPTS,
    )


def requeue_dead_job(
    job_id: uuid.UUID | str,
    *,
    now: datetime | None = None,
) -> CollectionJob:
    current = now or timezone.now()
    with transaction.atomic():
        job = CollectionJob.objects.select_for_update().get(pk=job_id)
        if job.status != CollectionJobStatus.DEAD:
            raise ValueError("Somente jobs esgotados podem ser reenfileirados.")
        job.status = CollectionJobStatus.QUEUED
        job.available_at = current
        job.max_attempts = job.attempt_count + settings.COLLECTION_JOB_MAX_ATTEMPTS
        job.finished_at = None
        job.claimed_by = ""
        job.claimed_at = None
        job.lease_expires_at = None
        job.last_error_code = ""
        job.last_error_message = ""
        job.save(
            update_fields=[
                "status",
                "available_at",
                "max_attempts",
                "finished_at",
                "claimed_by",
                "claimed_at",
                "lease_expires_at",
                "last_error_code",
                "last_error_message",
                "updated_at",
            ]
        )
        _sync_manual_refresh_requests(job, "PENDING", current)
    return job


def build_worker_id() -> str:
    hostname = socket.gethostname()[:48] or "worker"
    return f"{hostname}:{os.getpid()}:{uuid.uuid4().hex[:12]}"


def retry_delay_seconds(attempt_number: int) -> int:
    exponent = max(0, attempt_number - 1)
    return min(
        settings.COLLECTION_RETRY_MAX_SECONDS,
        settings.COLLECTION_RETRY_BASE_SECONDS * (2**exponent),
    )


def _acquire_lease(*, worker_id: str, now: datetime) -> bool:
    CollectionWorkerLease.objects.get_or_create(key=COLLECTION_LEASE_KEY)
    locked_until = now + timedelta(seconds=settings.COLLECTION_JOB_LEASE_SECONDS)
    updated = CollectionWorkerLease.objects.filter(key=COLLECTION_LEASE_KEY).filter(
        Q(locked_until__isnull=True)
        | Q(locked_until__lte=now)
        | Q(owner=worker_id)
    ).update(
        owner=worker_id,
        acquired_at=now,
        locked_until=locked_until,
    )
    return updated == 1


def _release_lease(*, worker_id: str) -> None:
    CollectionWorkerLease.objects.filter(
        key=COLLECTION_LEASE_KEY,
        owner=worker_id,
    ).update(owner="", acquired_at=None, locked_until=None)


def _sync_manual_refresh_requests(job: CollectionJob, status: str, now: datetime) -> None:
    """Keep a Telegram /atualizar request in sync without coupling app imports."""
    from telegram_bots.models import ManualRefreshRequest, ManualRefreshStatus

    if status == ManualRefreshStatus.PENDING:
        ManualRefreshRequest.objects.filter(collection_job=job).update(
            status=status,
            finished_at=None,
        )
    elif status == ManualRefreshStatus.PROCESSING:
        ManualRefreshRequest.objects.filter(
            collection_job=job,
            status=ManualRefreshStatus.PENDING,
        ).update(status=status, finished_at=None)
    elif status in {ManualRefreshStatus.COMPLETED, ManualRefreshStatus.FAILED}:
        ManualRefreshRequest.objects.filter(collection_job=job).exclude(
            status=status,
        ).update(status=status, finished_at=now)


def _recover_expired_jobs(*, now: datetime) -> int:
    recovered = 0
    stale_jobs = list(
        CollectionJob.objects.select_for_update()
        .filter(
            status=CollectionJobStatus.RUNNING,
            lease_expires_at__lte=now,
        )
        .order_by("scheduled_for", "created_at")
    )
    for job in stale_jobs:
        attempt = job.attempts.filter(
            number=job.attempt_count,
            status=CollectionJobAttemptStatus.RUNNING,
        ).first()
        if attempt is not None:
            attempt.finished_at = now
            attempt.status = CollectionJobAttemptStatus.ABANDONED
            attempt.duration_ms = max(
                0,
                int((now - attempt.started_at).total_seconds() * 1000),
            )
            attempt.error_code = "WORKER_LEASE_EXPIRED"
            attempt.error_message = "O worker deixou de renovar ou concluir a reserva."
            attempt.save(
                update_fields=[
                    "finished_at",
                    "status",
                    "duration_ms",
                    "error_code",
                    "error_message",
                ]
            )

        exhausted = job.attempt_count >= job.max_attempts
        job.status = (
            CollectionJobStatus.DEAD
            if exhausted
            else CollectionJobStatus.RETRY_WAIT
        )
        job.available_at = now + timedelta(
            seconds=retry_delay_seconds(job.attempt_count)
        )
        job.claimed_by = ""
        job.claimed_at = None
        job.lease_expires_at = None
        job.last_error_code = "WORKER_LEASE_EXPIRED"
        job.last_error_message = "Execucao interrompida antes da conclusao."
        job.finished_at = now if exhausted else None
        job.save(
            update_fields=[
                "status",
                "available_at",
                "claimed_by",
                "claimed_at",
                "lease_expires_at",
                "last_error_code",
                "last_error_message",
                "finished_at",
                "updated_at",
            ]
        )
        if exhausted:
            _sync_manual_refresh_requests(
                job,
                "FAILED",
                now,
            )
        recovered += 1
    return recovered


def _claim_next_job(*, worker_id: str, now: datetime) -> CollectionJob | None:
    candidate = (
        CollectionJob.objects.select_for_update()
        .filter(
            status__in=(
                CollectionJobStatus.QUEUED,
                CollectionJobStatus.RETRY_WAIT,
            ),
            available_at__lte=now,
        )
        .order_by("scheduled_for", "created_at", "id")
        .first()
    )
    if candidate is None:
        return None

    old_status = candidate.status
    old_attempt_count = candidate.attempt_count
    attempt_number = old_attempt_count + 1
    lease_expires_at = now + timedelta(
        seconds=settings.COLLECTION_JOB_LEASE_SECONDS
    )
    updated = CollectionJob.objects.filter(
        pk=candidate.pk,
        status=old_status,
        attempt_count=old_attempt_count,
    ).update(
        status=CollectionJobStatus.RUNNING,
        attempt_count=attempt_number,
        claimed_by=worker_id,
        claimed_at=now,
        lease_expires_at=lease_expires_at,
        finished_at=None,
    )
    if updated != 1:
        return None
    candidate.refresh_from_db()
    CollectionJobAttempt.objects.create(
        job=candidate,
        number=attempt_number,
        worker_id=worker_id,
        started_at=now,
    )
    _sync_manual_refresh_requests(candidate, "PROCESSING", now)
    return candidate


def _safe_failure(exc: Exception) -> tuple[str, str, bool]:
    if isinstance(exc, CollectionExecutionError):
        return (
            "COLLECTION_EXECUTION_ERROR",
            "O motor compartilhado encerrou o lote com falha.",
            exc.retryable,
        )
    if isinstance(exc, ImproperlyConfigured):
        return (
            "CONFIGURATION_ERROR",
            "A configuracao do worker e invalida.",
            False,
        )
    if isinstance(exc, (ValueError, TypeError)):
        return (
            "NON_RETRYABLE_ERROR",
            f"Falha nao recuperavel ({type(exc).__name__}).",
            False,
        )
    if isinstance(exc, TimeoutError):
        return "WORKER_TIMEOUT", "A execucao excedeu o tempo permitido.", True
    return (
        "WORKER_ERROR",
        f"Falha transitoria do worker ({type(exc).__name__}).",
        True,
    )


def _finish_success(
    *,
    job: CollectionJob,
    result: CollectionResult,
    finished_at: datetime,
    duration_ms: int,
) -> None:
    batch = CollectionBatch.objects.filter(pk=result.batch_id).first()
    attempt = CollectionJobAttempt.objects.get(
        job=job,
        number=job.attempt_count,
    )
    attempt.finished_at = finished_at
    attempt.status = CollectionJobAttemptStatus.SUCCESS
    attempt.duration_ms = duration_ms
    attempt.collection_batch = batch
    attempt.save(
        update_fields=[
            "finished_at",
            "status",
            "duration_ms",
            "collection_batch",
        ]
    )
    job.status = CollectionJobStatus.SUCCESS
    job.finished_at = finished_at
    job.duration_ms += duration_ms
    job.collection_batch = batch
    job.claimed_by = ""
    job.claimed_at = None
    job.lease_expires_at = None
    job.last_error_code = ""
    job.last_error_message = ""
    job.save(
        update_fields=[
            "status",
            "finished_at",
            "duration_ms",
            "collection_batch",
            "claimed_by",
            "claimed_at",
            "lease_expires_at",
            "last_error_code",
            "last_error_message",
            "updated_at",
        ]
    )
    _sync_manual_refresh_requests(job, "COMPLETED", finished_at)


def _finish_failure(
    *,
    job: CollectionJob,
    exc: Exception,
    finished_at: datetime,
    duration_ms: int,
) -> tuple[str, str]:
    error_code, error_message, retryable = _safe_failure(exc)
    exhausted = job.attempt_count >= job.max_attempts
    should_retry = retryable and not exhausted
    attempt = CollectionJobAttempt.objects.get(
        job=job,
        number=job.attempt_count,
    )
    attempt.finished_at = finished_at
    attempt.status = CollectionJobAttemptStatus.FAILED
    attempt.duration_ms = duration_ms
    attempt.error_code = error_code
    attempt.error_message = error_message
    batch_id = getattr(exc, "batch_id", None)
    batch = CollectionBatch.objects.filter(pk=batch_id).first() if batch_id else None
    attempt.collection_batch = batch
    attempt.save(
        update_fields=[
            "finished_at",
            "status",
            "duration_ms",
            "error_code",
            "error_message",
            "collection_batch",
        ]
    )

    job.status = (
        CollectionJobStatus.RETRY_WAIT
        if should_retry
        else CollectionJobStatus.DEAD
    )
    job.available_at = finished_at + timedelta(
        seconds=retry_delay_seconds(job.attempt_count)
    )
    job.finished_at = None if should_retry else finished_at
    job.duration_ms += duration_ms
    job.claimed_by = ""
    job.claimed_at = None
    job.lease_expires_at = None
    job.last_error_code = error_code
    job.last_error_message = error_message
    job.collection_batch = batch
    job.save(
        update_fields=[
            "status",
            "available_at",
            "finished_at",
            "duration_ms",
            "claimed_by",
            "claimed_at",
            "lease_expires_at",
            "last_error_code",
            "last_error_message",
            "collection_batch",
            "updated_at",
        ]
    )
    if not should_retry:
        _sync_manual_refresh_requests(job, "FAILED", finished_at)
    return error_code, job.status


def collect_and_deliver() -> CollectionResult:
    """Run the shared collector, then persist Telegram delivery outcomes."""
    result = collect_active_offers()
    # Delivery failures are persisted per event and must not turn a successful
    # collection into a retry of the entire batch.
    from telegram_bots.delivery import deliver_pending_alerts

    deliver_pending_alerts()
    return result


def process_next_job(
    *,
    collector: Collector | None = None,
    worker_id: str | None = None,
    wall_clock: Callable[[], datetime] = timezone.now,
    monotonic_clock: Callable[[], float] = time.monotonic,
) -> WorkerResult:
    resolved_worker_id = worker_id or build_worker_id()
    started_at = wall_clock()
    with transaction.atomic():
        if not _acquire_lease(worker_id=resolved_worker_id, now=started_at):
            return WorkerResult(state="BUSY")
        _recover_expired_jobs(now=started_at)
        job = _claim_next_job(worker_id=resolved_worker_id, now=started_at)

    if job is None:
        _release_lease(worker_id=resolved_worker_id)
        return WorkerResult(state="IDLE")

    LOGGER.info(
        "collection_job_started",
        extra={
            "event_code": "COLLECTION_JOB_STARTED",
            "job_id": str(job.pk),
            "attempt": job.attempt_count,
        },
    )

    timer_started = monotonic_clock()
    resolved_collector = collector or collect_and_deliver
    try:
        result = resolved_collector()
    except Exception as exc:
        finished_at = wall_clock()
        duration_ms = max(0, int((monotonic_clock() - timer_started) * 1000))
        try:
            error_code, job_status = _finish_failure(
                job=job,
                exc=exc,
                finished_at=finished_at,
                duration_ms=duration_ms,
            )
        finally:
            _release_lease(worker_id=resolved_worker_id)
        LOGGER.warning(
            "collection_job_failed",
            extra={
                "event_code": error_code,
                "status": job_status,
                "job_id": str(job.pk),
                "attempt": job.attempt_count,
                "duration_ms": duration_ms,
            },
        )
        return WorkerResult(
            state="FAILED",
            job_id=str(job.pk),
            job_status=job_status,
            attempt_number=job.attempt_count,
            error_code=error_code,
        )

    finished_at = wall_clock()
    duration_ms = max(0, int((monotonic_clock() - timer_started) * 1000))
    try:
        _finish_success(
            job=job,
            result=result,
            finished_at=finished_at,
            duration_ms=duration_ms,
        )
    finally:
        _release_lease(worker_id=resolved_worker_id)
    LOGGER.info(
        "collection_job_succeeded",
        extra={
            "event_code": "COLLECTION_JOB_SUCCEEDED",
            "status": CollectionJobStatus.SUCCESS,
            "job_id": str(job.pk),
            "batch_id": result.batch_id,
            "attempt": job.attempt_count,
            "duration_ms": duration_ms,
        },
    )
    return WorkerResult(
        state="SUCCESS",
        job_id=str(job.pk),
        job_status=CollectionJobStatus.SUCCESS,
        attempt_number=job.attempt_count,
        batch_id=result.batch_id,
    )
