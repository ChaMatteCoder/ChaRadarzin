from __future__ import annotations

import unittest
from datetime import datetime
from decimal import Decimal

from app.comparison import choose_best_offer
from app.models import OfferObservation


def offer(store: str, price: str, shipping: str, delivery: int) -> OfferObservation:
    return OfferObservation(
        observed_at=datetime.now().astimezone(),
        product_id="TEST001",
        store=store,
        seller=store,
        payment_method="PIX",
        product_price=Decimal(price),
        shipping_price=Decimal(shipping),
        delivery_min_days=1,
        delivery_max_days=delivery,
        in_stock=True,
        url="https://example.com/product",
        status="OK",
        parser_version="test",
    )


class ComparisonTest(unittest.TestCase):
    def test_chooses_lowest_total_instead_of_lowest_product_price(self) -> None:
        best = choose_best_offer(
            [offer("Loja A", "100.00", "50.00", 2), offer("Loja B", "120.00", "10.00", 5)]
        )
        self.assertIsNotNone(best)
        self.assertEqual(best.store, "Loja B")

    def test_uses_delivery_as_tiebreaker(self) -> None:
        best = choose_best_offer(
            [offer("Loja A", "120.00", "10.00", 6), offer("Loja B", "125.00", "5.00", 3)]
        )
        self.assertIsNotNone(best)
        self.assertEqual(best.store, "Loja B")

    def test_can_compare_product_price_before_shipping_stage(self) -> None:
        first = offer("Loja A", "100.00", "50.00", 2)
        second = OfferObservation(
            observed_at=first.observed_at,
            product_id="TEST001",
            store="Loja B",
            seller="Loja B",
            payment_method="PIX",
            product_price=Decimal("90.00"),
            shipping_price=None,
            delivery_min_days=None,
            delivery_max_days=None,
            in_stock=True,
            url="https://example.com/product",
            status="OK",
            parser_version="test",
        )
        best = choose_best_offer([first, second], require_shipping=False)
        self.assertIsNotNone(best)
        self.assertEqual(best.store, "Loja B")


if __name__ == "__main__":
    unittest.main()
