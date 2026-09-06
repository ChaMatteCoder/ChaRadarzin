from __future__ import annotations

from collections import Counter

from django.core.management.base import BaseCommand, CommandError

from telegram_bots.delivery import deliver_pending_alerts


class Command(BaseCommand):
    help = "Entrega alertas criados aos bots pessoais conectados, com idempotencia."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=100,
            help="Quantidade maxima de eventos processados nesta chamada.",
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        if limit < 1:
            raise CommandError("--limit deve ser positivo.")
        results = deliver_pending_alerts(limit=limit)
        counts = Counter(result.status for result in results)
        if not results:
            self.stdout.write("Nenhum alerta pendente encontrado.")
            return
        summary = ", ".join(
            f"{status.lower()}={counts[status]}"
            for status in sorted(counts)
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"{len(results)} alerta(s) processado(s): {summary}."
            )
        )
