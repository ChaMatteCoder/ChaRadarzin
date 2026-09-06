from django.core.management.base import BaseCommand, CommandError

from monitoring.collection_engine import collect_active_offers


class Command(BaseCommand):
    help = "Executa um lote isolado do motor compartilhado de coleta da Etapa 5."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            help="Limita fontes ativas nesta execucao manual de validacao.",
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        if limit is not None and limit < 1:
            raise CommandError("--limit deve ser um inteiro positivo.")
        result = collect_active_offers(max_sources=limit)
        self.stdout.write(
            self.style.SUCCESS(
                "Lote {batch}: {status}; URLs {fetched}/{planned}; "
                "fontes {sources}; tenants {tenants}; baselines {baselines}; "
                "alertas {alerts}.".format(
                    batch=result.batch_id,
                    status=result.status,
                    fetched=result.urls_fetched,
                    planned=result.urls_planned,
                    sources=result.sources_processed,
                    tenants=result.tenant_runs_count,
                    baselines=result.baselines_created,
                    alerts=result.alert_events_created,
                )
            )
        )
