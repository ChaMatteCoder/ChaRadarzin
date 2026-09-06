from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from monitoring.database_backup import manifest_path_for


class Command(BaseCommand):
    help = "Remove pares de backup/manifesto antigos do diretorio local controlado."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--confirm", action="store_true")
        parser.add_argument("--retention-days", type=int, default=settings.BACKUP_RETENTION_DAYS)

    def handle(self, *args, **options) -> None:
        days = options["retention_days"]
        if days < 1:
            raise CommandError("retention-days deve ser positivo.")
        backup_root = (settings.BASE_DIR / "backups").resolve()
        cutoff = timezone.now().timestamp() - timedelta(days=days).total_seconds()
        candidates: list[tuple[Path, Path]] = []
        for backup in backup_root.glob("chadaradzin-*.dump"):
            resolved = backup.resolve()
            manifest = manifest_path_for(resolved)
            if resolved.parent != backup_root or not manifest.is_file():
                continue
            if resolved.stat().st_mtime < cutoff:
                candidates.append((resolved, manifest.resolve()))

        self.stdout.write(f"retention_action={'PURGE' if options['confirm'] else 'DRY_RUN'}")
        self.stdout.write(f"backup_pairs={len(candidates)}")
        if not options["confirm"]:
            self.stdout.write("Nada foi excluido. Use --confirm para aplicar.")
            return
        for backup, manifest in candidates:
            if backup.parent != backup_root or manifest.parent != backup_root:
                continue
            backup.unlink()
            manifest.unlink()
        self.stdout.write(self.style.SUCCESS("Retencao de backups aplicada."))
