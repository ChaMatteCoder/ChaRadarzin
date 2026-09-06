from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone

from monitoring.models import (
    CollectionBatch,
    CollectionBatchStatus,
    CollectionJob,
    CollectionJobStatus,
    NotificationDelivery,
    NotificationDeliveryStatus,
)
from telegram_bots.models import (
    ManagedBot,
    ManagedBotStatus,
    TelegramUpdateReceipt,
    UpdateProcessingStatus,
)


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OperationalAlert:
    code: str
    severity: str
    message: str
    value: int | str | None = None


@dataclass(frozen=True, slots=True)
class OperationalSnapshot:
    generated_at: datetime
    lookback_hours: int
    metrics: dict[str, int | str | None]
    alerts: tuple[OperationalAlert, ...]

    @property
    def status(self) -> str:
        if any(alert.severity == "critical" for alert in self.alerts):
            return "critical"
        if self.alerts:
            return "warning"
        return "ok"

    def as_dict(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "status": self.status,
            "lookback_hours": self.lookback_hours,
            "metrics": self.metrics,
            "alerts": [asdict(alert) for alert in self.alerts],
        }


def build_operational_snapshot(*, now: datetime | None = None) -> OperationalSnapshot:
    current = now or timezone.now()
    cutoff = current - timedelta(hours=settings.OPERATIONAL_LOOKBACK_HOURS)
    stale_cutoff = current - timedelta(hours=settings.OPERATIONAL_COLLECTION_STALE_HOURS)

    latest_success = (
        CollectionBatch.objects.filter(
            status__in=(CollectionBatchStatus.SUCCESS, CollectionBatchStatus.PARTIAL)
        )
        .order_by("-finished_at", "-started_at")
        .first()
    )
    last_success_at = (
        (latest_success.finished_at or latest_success.started_at)
        if latest_success
        else None
    )
    dead_jobs = CollectionJob.objects.filter(status=CollectionJobStatus.DEAD).count()
    stale_running_jobs = CollectionJob.objects.filter(
        status=CollectionJobStatus.RUNNING,
        lease_expires_at__lt=current,
    ).count()
    failed_batches = CollectionBatch.objects.filter(
        status=CollectionBatchStatus.FAILED,
        started_at__gte=cutoff,
    ).count()
    partial_batches = CollectionBatch.objects.filter(
        status=CollectionBatchStatus.PARTIAL,
        started_at__gte=cutoff,
    ).count()
    failed_deliveries = NotificationDelivery.objects.filter(
        status=NotificationDeliveryStatus.FAILED,
        attempted_at__gte=cutoff,
    ).count()
    blocked_deliveries = NotificationDelivery.objects.filter(
        status=NotificationDeliveryStatus.BLOCKED,
        attempted_at__gte=cutoff,
    ).count()
    failed_webhooks = TelegramUpdateReceipt.objects.filter(
        status=UpdateProcessingStatus.FAILED,
        received_at__gte=cutoff,
    ).count()
    unhealthy_bots = ManagedBot.objects.filter(
        status__in=(
            ManagedBotStatus.ERROR,
            ManagedBotStatus.REVOKED,
            ManagedBotStatus.WEBHOOK_ERROR,
            ManagedBotStatus.OWNER_CHANGED,
        )
    ).count()

    metrics: dict[str, int | str | None] = {
        "collection_last_success_at": last_success_at.isoformat() if last_success_at else None,
        "collection_failed_recent": failed_batches,
        "collection_partial_recent": partial_batches,
        "jobs_queued": CollectionJob.objects.filter(
            status__in=(CollectionJobStatus.QUEUED, CollectionJobStatus.RETRY_WAIT)
        ).count(),
        "jobs_dead": dead_jobs,
        "jobs_stale_running": stale_running_jobs,
        "deliveries_failed_recent": failed_deliveries,
        "deliveries_blocked_recent": blocked_deliveries,
        "webhooks_failed_recent": failed_webhooks,
        "bots_unhealthy": unhealthy_bots,
    }
    alerts: list[OperationalAlert] = []
    if dead_jobs:
        alerts.append(
            OperationalAlert(
                "DEAD_COLLECTION_JOBS",
                "critical",
                "Há jobs de coleta que esgotaram as tentativas.",
                dead_jobs,
            )
        )
    if stale_running_jobs:
        alerts.append(
            OperationalAlert(
                "STALE_RUNNING_JOBS",
                "critical",
                "Há jobs em execução com lease expirado.",
                stale_running_jobs,
            )
        )
    if last_success_at is None:
        alerts.append(
            OperationalAlert(
                "NO_SUCCESSFUL_COLLECTION",
                "warning",
                "Nenhuma coleta compartilhada foi concluída com sucesso.",
            )
        )
    elif last_success_at < stale_cutoff:
        alerts.append(
            OperationalAlert(
                "COLLECTION_STALE",
                "warning",
                "A última coleta bem-sucedida está fora da janela esperada.",
                last_success_at.isoformat(),
            )
        )
    for code, count, message in (
        ("FAILED_COLLECTION_BATCHES", failed_batches, "Há coletas recentes com falha."),
        (
            "PARTIAL_COLLECTION_BATCHES",
            partial_batches,
            "Há coletas recentes com falhas parciais.",
        ),
        ("FAILED_TELEGRAM_DELIVERIES", failed_deliveries, "Há alertas recentes não entregues."),
        ("FAILED_TELEGRAM_WEBHOOKS", failed_webhooks, "Há updates recentes de webhook com falha."),
        ("UNHEALTHY_MANAGED_BOTS", unhealthy_bots, "Há bots pessoais em estado de erro."),
    ):
        if count:
            alerts.append(OperationalAlert(code, "warning", message, count))

    snapshot = OperationalSnapshot(
        generated_at=current,
        lookback_hours=settings.OPERATIONAL_LOOKBACK_HOURS,
        metrics=metrics,
        alerts=tuple(alerts),
    )
    LOGGER.info(
        "operational_snapshot_built",
        extra={
            "event_code": "OPERATIONAL_SNAPSHOT",
            "status": snapshot.status,
            "count": len(snapshot.alerts),
        },
    )
    return snapshot
