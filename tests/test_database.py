from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from app.database import RadarDatabase
from app.models import OfferObservation


class DatabaseTest(unittest.TestCase):
    def test_preserves_historical_low_and_previous_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = RadarDatabase(Path(directory) / "test.db")
            database.initialize()
            first_run = database.start_run("TEST", "input.xlsx")
            database.save_observations(first_run, [self._observation("100.00")])
            database.finish_run(first_run, status="SUCCESS", observations_count=1)
            second_run = database.start_run("TEST", "input.xlsx")
            database.save_observations(second_run, [self._observation("120.00")])

            self.assertEqual(database.historical_low("TEST001"), Decimal("100"))
            self.assertEqual(
                database.previous_best_total("TEST001", second_run), Decimal("100")
            )

    def test_separates_simulation_from_real_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = RadarDatabase(Path(directory) / "test.db")
            database.initialize()
            simulated = database.start_run("SIMULATION", "input.xlsx")
            database.save_observations(simulated, [self._observation("10.00")])
            database.finish_run(simulated, status="SUCCESS", observations_count=1)
            real = database.start_run("REAL", "input.xlsx")
            database.save_observations(real, [self._observation("100.00")])

            self.assertEqual(
                database.historical_low("TEST001", mode="REAL"), Decimal("100")
            )

    def test_tracks_failed_and_sent_notification_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = RadarDatabase(Path(directory) / "test.db")
            database.initialize()
            run_id = database.start_run("REAL_SHIPPING", "input.xlsx")
            database.record_notification_attempt(
                run_id,
                "TEST001",
                Decimal("90"),
                Decimal("100"),
                status="FAILED",
                error_message="temporario",
            )

            self.assertTrue(
                database.notification_attempt_exists(
                    "TEST001", Decimal("90"), status="FAILED"
                )
            )
            self.assertEqual(
                database.failed_notification_previous_price(
                    "TEST001", Decimal("90")
                ),
                Decimal("100"),
            )
            self.assertEqual(
                database.pending_failed_notification("TEST001", Decimal("90")),
                ("NEW_HISTORICAL_LOW", Decimal("100")),
            )

            database.record_notification_attempt(
                run_id,
                "TEST001",
                Decimal("90"),
                Decimal("100"),
                status="SENT",
            )
            self.assertTrue(
                database.notification_attempt_exists(
                    "TEST001", Decimal("90"), status="SENT"
                )
            )
            self.assertTrue(
                database.notification_sent_for_price("TEST001", Decimal("90"))
            )
            self.assertIsNone(
                database.pending_failed_notification("TEST001", Decimal("90"))
            )

    def test_identifies_previous_out_of_stock_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = RadarDatabase(Path(directory) / "test.db")
            database.initialize()
            first_run = database.start_run("REAL_SHIPPING", "input.xlsx")
            unavailable = self._observation("100.00", in_stock=False, status="OUT_OF_STOCK")
            database.save_observations(first_run, [unavailable])
            database.finish_run(first_run, status="SUCCESS", observations_count=1)
            second_run = database.start_run("REAL_SHIPPING", "input.xlsx")

            self.assertFalse(
                database.previous_product_stock_state(
                    "TEST001", second_run, mode="REAL_SHIPPING"
                )
            )

    def test_price_history_keeps_latest_best_offer_per_day(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = RadarDatabase(Path(directory) / "test.db")
            database.initialize()
            first_time = datetime.now().astimezone().replace(hour=8, minute=0)
            first_run = database.start_run("REAL_SHIPPING", "input.xlsx")
            database.save_observations(
                first_run,
                [
                    self._observation("100.00", observed_at=first_time, store="Loja A"),
                    self._observation("90.00", observed_at=first_time, store="Loja B"),
                ],
            )
            database.finish_run(first_run, status="SUCCESS", observations_count=2)
            second_run = database.start_run("REAL_SHIPPING", "input.xlsx")
            database.save_observations(
                second_run,
                [
                    self._observation(
                        "95.00",
                        observed_at=first_time + timedelta(hours=2),
                        store="Loja A",
                    )
                ],
            )
            database.finish_run(second_run, status="SUCCESS", observations_count=1)
            third_run = database.start_run("REAL_SHIPPING", "input.xlsx")
            database.save_observations(
                third_run,
                [
                    self._observation(
                        "85.00",
                        observed_at=first_time + timedelta(days=1),
                        store="Loja A",
                    )
                ],
            )
            database.finish_run(third_run, status="SUCCESS", observations_count=1)

            history = database.price_history("TEST001", mode="REAL_SHIPPING")
            store_history = database.price_history(
                "TEST001", mode="REAL_SHIPPING", by_store=True
            )

            self.assertEqual([point.price for point in history], [Decimal("95"), Decimal("85")])
            self.assertEqual(len(store_history), 3)

    @staticmethod
    def _observation(
        total: str,
        *,
        in_stock: bool = True,
        status: str = "OK",
        observed_at: datetime | None = None,
        store: str = "Loja",
    ) -> OfferObservation:
        return OfferObservation(
            observed_at=observed_at or datetime.now().astimezone(),
            product_id="TEST001",
            store=store,
            seller="Loja",
            payment_method="PIX",
            product_price=Decimal(total),
            shipping_price=Decimal("0"),
            delivery_min_days=1,
            delivery_max_days=2,
            in_stock=in_stock,
            url="https://example.com/product",
            status=status,
            parser_version="test",
        )


if __name__ == "__main__":
    unittest.main()
