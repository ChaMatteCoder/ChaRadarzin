from __future__ import annotations

import unittest
from pathlib import Path

from app.spreadsheet import load_catalog


class SpreadsheetTest(unittest.TestCase):
    def test_model_workbook_is_valid(self) -> None:
        workbook = Path(__file__).resolve().parent.parent / "produtos.xlsx"
        catalog = load_catalog(workbook)
        self.assertGreaterEqual(len(catalog.products), 1)
        self.assertGreaterEqual(len(catalog.links), 2)
        self.assertTrue(all(catalog.links_for(product.product_id) for product in catalog.active_products))


if __name__ == "__main__":
    unittest.main()
