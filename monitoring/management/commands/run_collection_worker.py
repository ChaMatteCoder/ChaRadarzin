from __future__ import annotations

import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from monitoring.job_queue import build_worker_id, process_next_job


class Command(BaseCommand):
    help = "Processa jobs persistidos da fila compartilhada de coleta."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Processa no maximo um job e encerra.",
        )
        parser.add_argument(
            "--poll-seconds",
            type=float,
            default=settings.COLLECTION_WORKER_POLL_SECONDS,
            help="Intervalo do worker continuo quando a fila esta vazia.",
        )

    def handle(self, *args, **options):
        poll_seconds = options["poll_seconds"]
        if poll_seconds <= 0:
            raise CommandError("--poll-seconds deve ser positivo.")

        worker_id = build_worker_id()
        once = options["once"]
        while True:
            result = process_next_job(worker_id=worker_id)
            if result.state == "SUCCESS":
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Job {result.job_id} concluido na tentativa "
                        f"{result.attempt_number}; lote {result.batch_id}."
                    )
                )
            elif result.state == "FAILED":
                raise CommandError(
                    f"Job {result.job_id} falhou ({result.error_code}); "
                    f"estado {result.job_status}."
                )
            elif result.state == "BUSY":
                self.stdout.write("Outro worker possui a reserva de coleta.")
            elif once:
                self.stdout.write("Nenhum job disponivel.")

            if once:
                return
            time.sleep(poll_seconds)
