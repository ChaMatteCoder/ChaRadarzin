from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError

from monitoring.job_queue import requeue_dead_job
from monitoring.models import CollectionJob


class Command(BaseCommand):
    help = "Reenfileira um job DEAD apos revisao operacional da causa."

    def add_arguments(self, parser):
        parser.add_argument("job_id", help="UUID exato do job esgotado.")

    def handle(self, *args, **options):
        try:
            job = requeue_dead_job(options["job_id"])
        except (CollectionJob.DoesNotExist, ValidationError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"Job {job.pk} reenfileirado; proxima tentativa "
                f"{job.attempt_count + 1}/{job.max_attempts}."
            )
        )
