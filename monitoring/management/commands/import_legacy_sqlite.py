from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from monitoring.models import (
    CollectorError,
    LegacyImportBatch,
    MonitoredProduct,
    MonitoringRun,
    NotificationDelivery,
    OfferSource,
    PriceObservation,
)
from tenancy.models import PaymentMethod, Tenant


LEGACY_TABLES = ("runs", "observations", "errors", "notification_attempts")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _as_aware(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


def _rows(connection: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    return connection.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()


class Command(BaseCommand):
    help = "Importa o SQLite do MVP sem modificar ou apagar o arquivo original."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--source", required=True, help="Caminho do SQLite legado")
        parser.add_argument(
            "--tenant",
            help="UUID do tenant de destino; obrigatorio fora do dry-run",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Valida origem e contagens sem gravar no banco novo",
        )

    def handle(self, *args, **options) -> None:
        source = Path(options["source"]).expanduser().resolve()
        if not source.is_file():
            raise CommandError("SQLite legado nao encontrado.")
        tenant: Tenant | None = None
        if options["tenant"]:
            try:
                tenant = Tenant.objects.get(pk=options["tenant"])
            except (Tenant.DoesNotExist, ValueError) as exc:
                raise CommandError("Tenant de destino nao encontrado.") from exc
        elif not options["dry_run"]:
            raise CommandError("--tenant e obrigatorio para gravar a importacao.")

        source_hash = _sha256(source)
        if tenant is not None and LegacyImportBatch.objects.filter(
            tenant=tenant,
            source_sha256=source_hash,
        ).exists():
            self.stdout.write(self.style.WARNING("Este arquivo ja foi importado."))
            return

        source_uri = f"{source.as_uri()}?mode=ro&immutable=1"
        try:
            connection = sqlite3.connect(source_uri, uri=True)
        except sqlite3.Error as exc:
            raise CommandError("Nao foi possivel abrir o SQLite em modo somente leitura.") from exc
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA query_only = ON")
            existing_tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            missing_tables = set(LEGACY_TABLES) - existing_tables
            if missing_tables:
                raise CommandError(
                    "SQLite incompativel; tabelas ausentes: "
                    + ", ".join(sorted(missing_tables))
                )
            legacy_rows = {table: _rows(connection, table) for table in LEGACY_TABLES}
        except sqlite3.Error as exc:
            raise CommandError("Falha ao validar a estrutura do SQLite legado.") from exc
        finally:
            connection.close()

        source_counts = {table: len(rows) for table, rows in legacy_rows.items()}
        if options["dry_run"]:
            self.stdout.write(
                self.style.SUCCESS(
                    "SQLite valido em modo somente leitura: "
                    + ", ".join(
                        f"{table}={count}" for table, count in source_counts.items()
                    )
                )
            )
            return

        if tenant is None:  # Garantido pela validacao acima; mantem o tipo explicito.
            raise CommandError("Tenant de destino ausente.")
        with transaction.atomic():
            products = self._import_products(tenant, legacy_rows)
            runs = self._import_runs(tenant, legacy_rows["runs"])
            sources = self._import_sources(
                tenant,
                products,
                legacy_rows["observations"],
            )
            observation_count, alert_cursor_at = self._import_observations(
                tenant,
                products,
                runs,
                sources,
                legacy_rows["observations"],
            )
            error_count = self._import_errors(
                tenant,
                runs,
                legacy_rows["errors"],
            )
            delivery_count = self._import_deliveries(
                tenant,
                products,
                runs,
                legacy_rows["notification_attempts"],
            )
            cursor = timezone.now()
            tenant.alerting_not_before = cursor
            tenant.save(update_fields=["alerting_not_before", "updated_at"])
            imported_counts = {
                "products": len(products),
                "sources": len(sources),
                "runs": len(runs),
                "observations": observation_count,
                "errors": error_count,
                "notification_attempts": delivery_count,
            }
            LegacyImportBatch.objects.create(
                tenant=tenant,
                source_file_name=source.name,
                source_sha256=source_hash,
                source_counts=source_counts,
                imported_counts=imported_counts,
                alert_cursor_at=alert_cursor_at or cursor,
            )

        self.stdout.write(
            self.style.SUCCESS(
                "Importacao concluida sem alterar o SQLite: "
                + ", ".join(
                    f"{table}={count}" for table, count in imported_counts.items()
                )
            )
        )

    @staticmethod
    def _import_products(
        tenant: Tenant,
        legacy_rows: dict[str, list[sqlite3.Row]],
    ) -> dict[str, MonitoredProduct]:
        product_ids = {
            str(row["product_id"]) for row in legacy_rows["observations"]
        } | {
            str(row["product_id"]) for row in legacy_rows["notification_attempts"]
        }
        preferred_payments: dict[str, str] = {}
        for row in legacy_rows["observations"]:
            product_id = str(row["product_id"])
            payment = str(row["payment_method"]).upper()
            if payment in PaymentMethod.values:
                preferred_payments.setdefault(product_id, payment)
        products: dict[str, MonitoredProduct] = {}
        for product_id in sorted(product_ids):
            products[product_id] = MonitoredProduct.objects.create(
                tenant=tenant,
                legacy_product_id=product_id,
                name=product_id,
                preferred_payment_method=preferred_payments.get(
                    product_id, PaymentMethod.PIX
                ),
            )
        return products

    @staticmethod
    def _import_runs(
        tenant: Tenant,
        rows: list[sqlite3.Row],
    ) -> dict[int, MonitoringRun]:
        runs: dict[int, MonitoringRun] = {}
        for row in rows:
            legacy_id = int(row["id"])
            runs[legacy_id] = MonitoringRun.objects.create(
                tenant=tenant,
                legacy_id=legacy_id,
                started_at=_as_aware(row["started_at"]),
                finished_at=_as_aware(row["finished_at"]),
                status=row["status"],
                mode=row["mode"],
                input_file_name=Path(row["input_file"]).name,
                products_count=row["products_count"],
                links_count=row["links_count"],
                observations_count=row["observations_count"],
                error_message=row["error_message"] or "",
            )
        return runs

    @staticmethod
    def _import_sources(
        tenant: Tenant,
        products: dict[str, MonitoredProduct],
        rows: list[sqlite3.Row],
    ) -> dict[tuple[str, str], OfferSource]:
        sources: dict[tuple[str, str], OfferSource] = {}
        for row in rows:
            key = (str(row["product_id"]), str(row["url"]))
            if key in sources:
                continue
            sources[key] = OfferSource.objects.create(
                tenant=tenant,
                product=products[key[0]],
                store=row["store"],
                url=key[1],
            )
        return sources

    @staticmethod
    def _import_observations(
        tenant: Tenant,
        products: dict[str, MonitoredProduct],
        runs: dict[int, MonitoringRun],
        sources: dict[tuple[str, str], OfferSource],
        rows: list[sqlite3.Row],
    ) -> tuple[int, datetime | None]:
        latest: datetime | None = None
        for row in rows:
            product_id = str(row["product_id"])
            observed_at = _as_aware(row["observed_at"])
            if observed_at is not None and (latest is None or observed_at > latest):
                latest = observed_at
            PriceObservation.objects.create(
                tenant=tenant,
                legacy_id=row["id"],
                run=runs[int(row["run_id"])],
                product=products[product_id],
                source=sources[(product_id, str(row["url"]))],
                observed_at=observed_at,
                seller=row["seller"],
                payment_method=row["payment_method"],
                product_price_cents=row["product_price_cents"],
                shipping_price_cents=row["shipping_price_cents"],
                total_price_cents=row["total_price_cents"],
                delivery_min_days=row["delivery_min_days"],
                delivery_max_days=row["delivery_max_days"],
                in_stock=bool(row["in_stock"]),
                status=row["status"],
                parser_version=row["parser_version"],
                error_message=row["error_message"] or "",
            )
        return len(rows), latest

    @staticmethod
    def _import_errors(
        tenant: Tenant,
        runs: dict[int, MonitoringRun],
        rows: list[sqlite3.Row],
    ) -> int:
        for row in rows:
            run_id = row["run_id"]
            CollectorError.objects.create(
                tenant=tenant,
                legacy_id=row["id"],
                run=runs.get(int(run_id)) if run_id is not None else None,
                occurred_at=_as_aware(row["occurred_at"]),
                context=row["context"],
                message=row["message"],
            )
        return len(rows)

    @staticmethod
    def _import_deliveries(
        tenant: Tenant,
        products: dict[str, MonitoredProduct],
        runs: dict[int, MonitoringRun],
        rows: list[sqlite3.Row],
    ) -> int:
        for row in rows:
            run_id = row["run_id"]
            NotificationDelivery.objects.create(
                tenant=tenant,
                legacy_id=row["id"],
                run=runs.get(int(run_id)) if run_id is not None else None,
                product=products[str(row["product_id"])],
                attempted_at=_as_aware(row["attempted_at"]),
                channel=row["channel"],
                event_type=row["event_type"],
                price_cents=row["price_cents"],
                previous_price_cents=row["previous_price_cents"],
                status=row["status"],
                error_message=row["error_message"] or "",
            )
        return len(rows)
