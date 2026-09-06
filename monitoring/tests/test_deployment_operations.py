from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db import DatabaseError
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from monitoring.database_backup import (
    create_postgres_backup,
    restore_postgres_backup,
    verify_backup_artifact,
)


POSTGRES_SETTINGS = {
    "ENGINE": "django.db.backends.postgresql",
    "NAME": "radar",
    "USER": "radar_user",
    "PASSWORD": "never-in-argv",
    "HOST": "db",
    "PORT": "5432",
    "OPTIONS": {"sslmode": "require"},
}


@override_settings(SECURE_SSL_REDIRECT=False)
class HealthEndpointTest(TestCase):
    def test_liveness_and_readiness_are_public_and_minimal(self):
        client = Client()

        live = client.get("/health/live/")
        ready = client.get("/health/ready/")

        self.assertEqual(live.status_code, 200)
        self.assertEqual(live.json(), {"status": "ok"})
        self.assertEqual(ready.status_code, 200)
        self.assertEqual(ready.json(), {"status": "ok"})

    def test_readiness_fails_closed_without_exposing_database_error(self):
        with patch(
            "chadaradzin.health.connection.cursor",
            side_effect=DatabaseError("secret database detail"),
        ):
            response = Client().get("/health/ready/")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})
        self.assertNotContains(response, "secret", status_code=503)


class DatabaseBackupTest(SimpleTestCase):
    def test_backup_uses_pgpassword_and_writes_verified_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "stage11.dump"
            captured = {}

            def fake_runner(command, **kwargs):
                captured["command"] = command
                captured["environment"] = kwargs["env"]
                Path(command[command.index("--file") + 1]).write_bytes(b"controlled-backup")
                return subprocess.CompletedProcess(command, 0)

            artifact = create_postgres_backup(
                output,
                database=POSTGRES_SETTINGS,
                runner=fake_runner,
            )

            self.assertNotIn("never-in-argv", captured["command"])
            self.assertEqual(captured["environment"]["PGPASSWORD"], "never-in-argv")
            self.assertEqual(captured["environment"]["PGSSLMODE"], "require")
            self.assertEqual(verify_backup_artifact(output), artifact)
            manifest = json.loads(artifact.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["sha256"], artifact.sha256)

    def test_tampered_backup_is_rejected_before_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "stage11.dump"

            def fake_dump(command, **_kwargs):
                Path(command[command.index("--file") + 1]).write_bytes(b"original")
                return subprocess.CompletedProcess(command, 0)

            create_postgres_backup(
                output,
                database=POSTGRES_SETTINGS,
                runner=fake_dump,
            )
            output.write_bytes(b"tampered")

            with self.assertRaisesRegex(ValueError, "SHA-256"):
                restore_postgres_backup(
                    output,
                    database=POSTGRES_SETTINGS,
                    runner=lambda *_args, **_kwargs: self.fail("restore executado"),
                )

    def test_restore_uses_atomic_clean_restore_without_password_argument(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "stage11.dump"

            def fake_dump(command, **_kwargs):
                Path(command[command.index("--file") + 1]).write_bytes(b"backup")
                return subprocess.CompletedProcess(command, 0)

            create_postgres_backup(
                output,
                database=POSTGRES_SETTINGS,
                runner=fake_dump,
            )
            captured = {}

            def fake_restore(command, **kwargs):
                captured["command"] = command
                captured["environment"] = kwargs["env"]
                return subprocess.CompletedProcess(command, 0)

            restore_postgres_backup(
                output,
                database=POSTGRES_SETTINGS,
                runner=fake_restore,
            )

            self.assertIn("--single-transaction", captured["command"])
            self.assertIn("--clean", captured["command"])
            self.assertNotIn("never-in-argv", captured["command"])
            self.assertEqual(captured["environment"]["PGPASSWORD"], "never-in-argv")

    def test_backup_refuses_non_postgresql_database(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ImproperlyConfigured):
                create_postgres_backup(
                    Path(directory) / "invalid.dump",
                    database={"ENGINE": "django.db.backends.sqlite3"},
                )

    def test_backup_retention_is_dry_run_until_confirmed(self):
        with tempfile.TemporaryDirectory() as directory:
            backup_root = Path(directory) / "backups"
            backup_root.mkdir()
            backup = backup_root / "chadaradzin-old.dump"
            manifest = backup_root / "chadaradzin-old.dump.json"
            backup.write_bytes(b"old")
            manifest.write_text("{}", encoding="utf-8")
            old = (timezone.now() - timedelta(days=40)).timestamp()
            os.utime(backup, (old, old))
            os.utime(manifest, (old, old))
            output = StringIO()

            with override_settings(BASE_DIR=Path(directory), BACKUP_RETENTION_DAYS=30):
                call_command("prune_backups", stdout=output)
                self.assertTrue(backup.exists())
                call_command("prune_backups", "--confirm", stdout=output)

            self.assertFalse(backup.exists())
            self.assertFalse(manifest.exists())
