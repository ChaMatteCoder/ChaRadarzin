from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from monitoring.operations import build_operational_snapshot


class Command(BaseCommand):
    help = "Exibe métricas e alertas operacionais sem incluir dados pessoais ou segredos."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--json", action="store_true", dest="as_json")
        parser.add_argument(
            "--fail-on",
            choices=("none", "warning", "critical"),
            default="none",
            help="Retorna erro quando o snapshot alcançar esta severidade.",
        )

    def handle(self, *args, **options) -> None:
        snapshot = build_operational_snapshot()
        if options["as_json"]:
            self.stdout.write(json.dumps(snapshot.as_dict(), ensure_ascii=False, sort_keys=True))
        else:
            self.stdout.write(f"status={snapshot.status}")
            for name, value in snapshot.metrics.items():
                self.stdout.write(f"metric.{name}={value}")
            for alert in snapshot.alerts:
                self.stdout.write(
                    f"alert.{alert.severity}.{alert.code}={alert.value if alert.value is not None else 1}"
                )

        fail_on = options["fail_on"]
        should_fail = (
            fail_on == "warning" and snapshot.status in {"warning", "critical"}
        ) or (fail_on == "critical" and snapshot.status == "critical")
        if should_fail:
            raise CommandError(f"Status operacional {snapshot.status}.")
