from __future__ import annotations

import json
import unittest
from datetime import datetime
from decimal import Decimal

from app.collectors.amazon import parse_amazon_html
from app.collectors.kabum import parse_kabum_html
from app.collectors.real import collect_real_observations
from app.models import Catalog, Product, ProductLink


AMAZON_HTML = """
<html><head><title>SSD Crucial BX500 CT1000BX500SSD1 1 TB SATA</title></head>
<body>
  <span id="productTitle">SSD Crucial BX500 CT1000BX500SSD1 1 TB SATA</span>
  <div id="corePriceDisplay_desktop_feature_div">
    <span class="a-price"><span class="a-offscreen">R$ 862,60</span></span>
    <span>5% off no Pix</span>
  </div>
  <a id='sellerProfileTriggerId'>BPS Oficial</a>
  <div id="availability"><span>Em estoque</span></div>
</body></html>
"""


KABUM_DATA = {
    "props": {
        "pageProps": {
            "product": {
                "title": "SSD Crucial BX500 CT1000BX500SSD1 1 TB SATA",
                "id": 167492,
                "sellerName": "TEITEC INFORMÁTICA",
                "sellerId": 3294,
                "offerIdMarketplace": 374498,
                "available": True,
                "prices": {"priceWithDiscount": 999},
            }
        }
    }
}
KABUM_HTML = (
    '<html><body><script id="__NEXT_DATA__" type="application/json">'
    + json.dumps(KABUM_DATA, ensure_ascii=False)
    + "</script><span>À vista no PIX</span></body></html>"
)


class CollectorParserTest(unittest.TestCase):
    def test_parses_amazon_offer(self) -> None:
        parsed = parse_amazon_html(AMAZON_HTML)
        self.assertEqual(parsed.product_price, Decimal("862.60"))
        self.assertEqual(parsed.seller, "BPS Oficial")
        self.assertTrue(parsed.in_stock)
        self.assertTrue(parsed.supports_pix)

    def test_parses_amazon_as_direct_seller(self) -> None:
        html = AMAZON_HTML.replace(
            "<a id='sellerProfileTriggerId'>BPS Oficial</a>",
            '<div id="merchantInfoFeature_feature_div">'
            '<span class="offer-display-feature-text-message">Amazon.com.br</span></div>',
        )
        self.assertEqual(parse_amazon_html(html).seller, "Amazon.com.br")

    def test_parses_kabum_next_data(self) -> None:
        parsed = parse_kabum_html(KABUM_HTML)
        self.assertEqual(parsed.product_price, Decimal("999.00"))
        self.assertEqual(parsed.seller, "TEITEC INFORMÁTICA")

    def test_collects_two_valid_stores_and_rejects_wrong_model(self) -> None:
        product = Product(
            product_id="SSD001",
            active=True,
            name="SSD Crucial BX500 1 TB SATA",
            exact_model="CT1000BX500SSD1",
            target_price=Decimal("900"),
            payment_method="PIX",
        )
        links = (
            ProductLink(
                product_id="SSD001",
                store="Amazon",
                url="https://www.amazon.com.br/dp/B07YD579WM",
                expected_seller="BPS Oficial",
                variant="CT1000BX500SSD1 / 1 TB / SATA",
            ),
            ProductLink(
                product_id="SSD001",
                store="KaBuM",
                url="https://www.kabum.com.br/produto/167492/ssd",
                expected_seller="TEITEC INFORMÁTICA",
                variant="CT1000BX500SSD1 / 1 TB / SATA",
            ),
        )

        def fetcher(url: str, **_: object) -> str:
            return AMAZON_HTML if "amazon" in url else KABUM_HTML

        observations = collect_real_observations(
            Catalog(products=(product,), links=links),
            observed_at=datetime.now().astimezone(),
            fetcher=fetcher,
        )
        self.assertEqual([offer.status for offer in observations], ["OK", "OK"])

        wrong_product = Product(
            product_id="MON001",
            active=True,
            name="Monitor AOC",
            exact_model="Q27G4F",
            target_price=Decimal("1700"),
            payment_method="PIX",
        )
        wrong_link = ProductLink(
            product_id="MON001",
            store="KaBuM",
            url="https://www.kabum.com.br/produto/924049/monitor",
            expected_seller="TEITEC INFORMÁTICA",
            variant="Q27G4F / QHD",
        )
        mismatch = collect_real_observations(
            Catalog(products=(wrong_product,), links=(wrong_link,)),
            fetcher=lambda *_args, **_kwargs: KABUM_HTML,
        )[0]
        self.assertEqual(mismatch.status, "MODEL_MISMATCH")

    def test_adds_shipping_to_valid_offer_when_requested(self) -> None:
        product = Product(
            product_id="SSD001",
            active=True,
            name="SSD Crucial BX500 1 TB SATA",
            exact_model="CT1000BX500SSD1",
            target_price=Decimal("900"),
            payment_method="PIX",
        )
        link = ProductLink(
            product_id="SSD001",
            store="KaBuM",
            url="https://www.kabum.com.br/produto/167492/ssd",
            expected_seller="TEITEC INFORMÁTICA",
            variant="CT1000BX500SSD1 / 1 TB / SATA",
        )

        def shipping_quoter(*_args: object, **_kwargs: object):
            from app.shipping import ShippingQuote

            return ShippingQuote(Decimal("20.89"), None, 3, "Expressa", "32")

        observation = collect_real_observations(
            Catalog(products=(product,), links=(link,)),
            fetcher=lambda *_args, **_kwargs: KABUM_HTML,
            destination_postal_code="01001000",
            shipping_quoter=shipping_quoter,
        )[0]
        self.assertEqual(observation.status, "OK")
        self.assertEqual(observation.shipping_price, Decimal("20.89"))
        self.assertEqual(observation.total_price, Decimal("1019.89"))
        self.assertEqual(observation.delivery_max_days, 3)


if __name__ == "__main__":
    unittest.main()
