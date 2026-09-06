from __future__ import annotations

import re

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from monitoring.job_queue import enqueue_collection_now, enqueue_daily_collection


class Command(BaseCommand):
    help = "Agenda de forma idempotente a coleta diaria compartilhada."

    def add_arguments(self, parser):
        parser.add_argument(
            "--at",
            dest="daily_at",
            help="Sobrescreve o horario diario desta chamada (HH:MM).",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Cria agora um job manual unico para validacao operacional.",
        )

    def handle(self, *args, **options):
        daily_at = options["daily_at"]
        if daily_at and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", daily_at):
            raise CommandError("--at deve usar o formato HH:MM.")

        if options["force"]:
            job = enqueue_collection_now()
            self.stdout.write(
                self.style.SUCCESS(f"Job manual {job.pk} adicionado a fila.")
            )
            return

        result = enqueue_daily_collection(now=timezone.now(), daily_at=daily_at)
        if not result.due:
            self.stdout.write("Coleta diaria ainda nao esta no horario.")
        elif result.created:
            self.stdout.write(
                self.style.SUCCESS(f"Job diario {result.job.pk} adicionado a fila.")
            )
        else:
            self.stdout.write(f"Job diario {result.job.pk} ja estava registrado.")
