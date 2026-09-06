from __future__ import annotations

import hashlib
import tempfile
from datetime import datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from app.database import RadarDatabase
from app.models import OfferObservation
from monitoring.models import (
    CollectorError,
    LegacyImportBatch,
    MonitoredProduct,
    MonitoringRun,
    NotificationDelivery,
    OfferSource,
    PriceObservation,
)
from tenancy.models import Tenant


class LegacyImportTest(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(name="Tenant legado")
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.source = Path(self.temporary_directory.name) / "radar_precos.db"
        database = RadarDatabase(self.source)
        database.initialize()
        run_id = database.start_run("REAL_WITH_SHIPPING", "C:/local/produtos.xlsx")
        database.save_observations(
            run_id,
            [
                OfferObservation(
                    observed_at=datetime.now().astimezone(),
                    product_id="SSD001",
                    store="Loja Teste",
                    seller="Vendedor Teste",
                    payment_method="PIX",
                    product_price=Decimal("100.00"),
                    shipping_price=Decimal("10.00"),
                    delivery_min_days=2,
                    delivery_max_days=4,
                    in_stock=True,
                    url="https://example.com/ssd",
                    status="OK",
                    parser_version="fixture-v1",
                )
            ],
        )
        database.record_error(run_id, "fixture", "erro sintetico")
        database.record_notification_attempt(
            run_id,
            "SSD001",
            Decimal("110.00"),
            Decimal("120.00"),
            status="SENT",
        )
        database.finish_run(
            run_id,
            status="SUCCESS",
            products_count=1,
            links_count=1,
            observations_count=1,
        )

    def _source_hash(self) -> str:
        return hashlib.sha256(self.source.read_bytes()).hexdigest()

    def test_import_is_read_only_reconciled_and_idempotent(self) -> None:
        original_hash = self._source_hash()
        output = StringIO()

        call_command(
            "import_legacy_sqlite",
            source=str(self.source),
            tenant=str(self.tenant.pk),
            stdout=output,
        )

        self.assertTrue(self.source.exists())
        self.assertEqual(self._source_hash(), original_hash)
        self.assertEqual(MonitoredProduct.objects.for_tenant(self.tenant).count(), 1)
        self.assertEqual(OfferSource.objects.for_tenant(self.tenant).count(), 1)
        self.assertEqual(MonitoringRun.objects.for_tenant(self.tenant).count(), 1)
        self.assertEqual(PriceObservation.objects.for_tenant(self.tenant).count(), 1)
        self.assertEqual(CollectorError.objects.for_tenant(self.tenant).count(), 1)
        self.assertEqual(NotificationDelivery.objects.for_tenant(self.tenant).count(), 1)

        batch = LegacyImportBatch.objects.get(tenant=self.tenant)
        self.assertEqual(batch.source_file_name, self.source.name)
        self.assertEqual(batch.source_counts["observations"], 1)
        self.assertEqual(batch.imported_counts["observations"], 1)
        self.assertIsNotNone(batch.alert_cursor_at)
        self.tenant.refresh_from_db()
        self.assertIsNotNone(self.tenant.alerting_not_before)

        call_command(
            "import_legacy_sqlite",
            source=str(self.source),
            tenant=str(self.tenant.pk),
            stdout=output,
        )
        self.assertEqual(LegacyImportBatch.objects.count(), 1)
        self.assertEqual(PriceObservation.objects.count(), 1)

    def test_dry_run_does_not_write_destination(self) -> None:
        original_hash = self._source_hash()

        call_command(
            "import_legacy_sqlite",
            source=str(self.source),
            dry_run=True,
            stdout=StringIO(),
        )

        self.assertEqual(self._source_hash(), original_hash)
        self.assertFalse(LegacyImportBatch.objects.exists())
        self.assertFalse(MonitoredProduct.objects.exists())

    def test_real_import_requires_explicit_destination_tenant(self) -> None:
        with self.assertRaisesMessage(CommandError, "--tenant e obrigatorio"):
            call_command(
                "import_legacy_sqlite",
                source=str(self.source),
                stdout=StringIO(),
            )
