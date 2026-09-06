from __future__ import annotations

import hashlib
import json
import os
import subprocess
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Mapping

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


Runner = Callable[..., subprocess.CompletedProcess]


@dataclass(frozen=True, slots=True)
class BackupArtifact:
    backup_path: Path
    manifest_path: Path
    sha256: str
    size_bytes: int


def _database_cli(database: Mapping[str, object]) -> tuple[list[str], dict[str, str]]:
    engine = str(database.get("ENGINE", ""))
    if "postgresql" not in engine:
        raise ImproperlyConfigured("Backup operacional exige PostgreSQL.")

    arguments: list[str] = []
    for flag, key in (
        ("--host", "HOST"),
        ("--port", "PORT"),
        ("--username", "USER"),
        ("--dbname", "NAME"),
    ):
        value = str(database.get(key, "") or "").strip()
        if value:
            arguments.extend((flag, value))

    environment = os.environ.copy()
    password = str(database.get("PASSWORD", "") or "")
    if password:
        environment["PGPASSWORD"] = password
    options = database.get("OPTIONS") or {}
    if isinstance(options, Mapping):
        sslmode = str(options.get("sslmode", "") or "").strip()
        if sslmode:
            environment["PGSSLMODE"] = sslmode
    environment.setdefault("PGCONNECT_TIMEOUT", "10")
    return arguments, environment


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_path_for(backup_path: Path) -> Path:
    return backup_path.with_suffix(backup_path.suffix + ".json")


def create_postgres_backup(
    output_path: Path,
    *,
    database: Mapping[str, object] | None = None,
    runner: Runner = subprocess.run,
    binary: str | None = None,
) -> BackupArtifact:
    resolved = output_path.resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved.with_name(f".{resolved.name}.{uuid.uuid4().hex}.tmp")
    cli_arguments, environment = _database_cli(database or settings.DATABASES["default"])
    command = [
        binary or os.environ.get("PG_DUMP_BINARY", "pg_dump"),
        *cli_arguments,
        "--format=custom",
        "--compress=9",
        "--no-owner",
        "--no-acl",
        "--file",
        str(temporary),
    ]
    try:
        runner(
            command,
            check=True,
            env=environment,
            capture_output=True,
            text=True,
        )
        if not temporary.exists() or temporary.stat().st_size < 1:
            raise RuntimeError("pg_dump nao produziu um artefato valido.")
        temporary.replace(resolved)
    finally:
        if temporary.exists():
            temporary.unlink()

    try:
        resolved.chmod(0o600)
    except OSError:
        pass
    checksum = sha256_file(resolved)
    size_bytes = resolved.stat().st_size
    manifest_path = manifest_path_for(resolved)
    manifest = {
        "backup_file": resolved.name,
        "created_at": datetime.now(tz=UTC).isoformat(),
        "database_engine": "postgresql",
        "format": "pg_dump_custom_v1",
        "sha256": checksum,
        "size_bytes": size_bytes,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    try:
        manifest_path.chmod(0o600)
    except OSError:
        pass
    return BackupArtifact(resolved, manifest_path, checksum, size_bytes)


def verify_backup_artifact(backup_path: Path) -> BackupArtifact:
    resolved = backup_path.resolve()
    manifest_path = manifest_path_for(resolved)
    if not resolved.is_file() or not manifest_path.is_file():
        raise ValueError("Backup ou manifesto correspondente nao encontrado.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("Manifesto do backup invalido.") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != "pg_dump_custom_v1":
        raise ValueError("Formato de backup nao reconhecido.")
    checksum = sha256_file(resolved)
    size_bytes = resolved.stat().st_size
    if manifest.get("backup_file") != resolved.name:
        raise ValueError("Manifesto pertence a outro arquivo de backup.")
    if manifest.get("sha256") != checksum or manifest.get("size_bytes") != size_bytes:
        raise ValueError("Integridade SHA-256 do backup nao confere.")
    return BackupArtifact(resolved, manifest_path, checksum, size_bytes)


def restore_postgres_backup(
    backup_path: Path,
    *,
    database: Mapping[str, object] | None = None,
    runner: Runner = subprocess.run,
    binary: str | None = None,
) -> BackupArtifact:
    artifact = verify_backup_artifact(backup_path)
    cli_arguments, environment = _database_cli(database or settings.DATABASES["default"])
    command = [
        binary or os.environ.get("PG_RESTORE_BINARY", "pg_restore"),
        *cli_arguments,
        "--clean",
        "--if-exists",
        "--exit-on-error",
        "--single-transaction",
        "--no-owner",
        "--no-acl",
        str(artifact.backup_path),
    ]
    runner(
        command,
        check=True,
        env=environment,
        capture_output=True,
        text=True,
    )
    return artifact
