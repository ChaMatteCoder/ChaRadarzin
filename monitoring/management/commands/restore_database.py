from __future__ import annotations

import subprocess
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from monitoring.database_backup import restore_postgres_backup, verify_backup_artifact


class Command(BaseCommand):
    help = "Valida ou restaura um backup PostgreSQL somente apos confirmacao explicita."

    def add_arguments(self, parser) -> None:
        parser.add_argument("backup")
        parser.add_argument("--verify-only", action="store_true")
        parser.add_argument("--confirm", action="store_true")

    def handle(self, *args, **options) -> None:
        backup = Path(options["backup"])
        try:
            artifact = verify_backup_artifact(backup)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        if options["verify_only"]:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Backup integro: {artifact.backup_path} ({artifact.size_bytes} bytes)."
                )
            )
            return
        if not options["confirm"]:
            raise CommandError(
                "Restore altera o banco atual. Pare web/workers e repita com --confirm."
            )
        connections.close_all()
        try:
            restore_postgres_backup(backup)
        except ImproperlyConfigured as exc:
            raise CommandError(str(exc)) from exc
        except FileNotFoundError as exc:
            raise CommandError("pg_restore nao foi encontrado neste ambiente.") from exc
        except subprocess.CalledProcessError as exc:
            raise CommandError("O pg_restore encerrou com falha.") from exc
        finally:
            connections.close_all()
        call_command("migrate", interactive=False, verbosity=options["verbosity"])
        self.stdout.write(self.style.SUCCESS("Restore concluido e migrations verificadas."))
