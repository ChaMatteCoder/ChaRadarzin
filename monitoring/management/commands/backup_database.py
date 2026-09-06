from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError

from monitoring.database_backup import create_postgres_backup


class Command(BaseCommand):
    help = "Cria backup PostgreSQL custom com manifesto e SHA-256."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--output")
        parser.add_argument("--force", action="store_true")

    def handle(self, *args, **options) -> None:
        timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
        output = Path(
            options["output"]
            or settings.BASE_DIR / "backups" / f"chadaradzin-{timestamp}.dump"
        )
        manifest = output.with_suffix(output.suffix + ".json")
        if not options["force"] and (output.exists() or manifest.exists()):
            raise CommandError("O destino ja existe; escolha outro nome ou use --force.")
        try:
            artifact = create_postgres_backup(output)
        except ImproperlyConfigured as exc:
            raise CommandError(str(exc)) from exc
        except FileNotFoundError as exc:
            raise CommandError("pg_dump nao foi encontrado neste ambiente.") from exc
        except (subprocess.CalledProcessError, RuntimeError) as exc:
            raise CommandError("Nao foi possivel concluir o backup PostgreSQL.") from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"Backup verificado: {artifact.backup_path} ({artifact.size_bytes} bytes)."
            )
        )
