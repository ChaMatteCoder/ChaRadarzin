from __future__ import annotations

import json
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from monitoring.operations import build_operational_snapshot


class Command(BaseCommand):
    help = "Publica snapshots operacionais periodicos e falha no limite configurado."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--once", action="store_true")
        parser.add_argument(
            "--interval-seconds",
            type=float,
            default=settings.OPERATIONAL_MONITOR_INTERVAL_SECONDS,
        )
        parser.add_argument(
            "--fail-on",
            choices=("none", "warning", "critical"),
            default="critical",
        )

    def handle(self, *args, **options) -> None:
        interval = options["interval_seconds"]
        if interval <= 0:
            raise CommandError("--interval-seconds deve ser positivo.")
        while True:
            snapshot = build_operational_snapshot()
            self.stdout.write(
                json.dumps(snapshot.as_dict(), ensure_ascii=False, sort_keys=True)
            )
            should_fail = (
                options["fail_on"] == "warning"
                and snapshot.status in {"warning", "critical"}
            ) or (
                options["fail_on"] == "critical" and snapshot.status == "critical"
            )
            if should_fail:
                raise CommandError(f"Status operacional {snapshot.status}.")
            if options["once"]:
                return
            time.sleep(interval)
