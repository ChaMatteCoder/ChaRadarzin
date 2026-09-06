from __future__ import annotations

import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from monitoring.job_queue import enqueue_daily_collection


LOGGER = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Mantem o agendamento diario idempotente em ambientes de servidor."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--once", action="store_true")
        parser.add_argument(
            "--poll-seconds",
            type=float,
            default=settings.COLLECTION_SCHEDULER_POLL_SECONDS,
        )

    def handle(self, *args, **options) -> None:
        poll_seconds = options["poll_seconds"]
        if poll_seconds <= 0:
            raise CommandError("--poll-seconds deve ser positivo.")
        while True:
            result = enqueue_daily_collection(now=timezone.now())
            if result.due and result.created:
                LOGGER.info(
                    "daily_collection_scheduled",
                    extra={
                        "event_code": "DAILY_COLLECTION_SCHEDULED",
                        "status": "QUEUED",
                        "job_id": str(result.job.pk),
                    },
                )
                self.stdout.write(self.style.SUCCESS("Job diario adicionado a fila."))
            if options["once"]:
                return
            time.sleep(poll_seconds)
