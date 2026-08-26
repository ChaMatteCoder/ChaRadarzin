from __future__ import annotations

import json
import unittest
from datetime import date
from decimal import Decimal

from app.models import Product, ProductLink
from app.shipping import (
    AmazonShippingProvider,
    KabumShippingProvider,
    ShippingNotAvailable,
    extract_kabum_shipping_context,
    parse_amazon_shipping_html,
    parse_kabum_shipping_response,
    validate_postal_code,
)


KABUM_HTML = """
<script id="__NEXT_DATA__" type="application/json">
{"props":{"pageProps":{"product":{"id":167492,"sellerName":"TEITEC INFORMÁTICA",
"sellerId":3294,"offerIdMarketplace":374498}}}}
</script>
"""


class ShippingTest(unittest.TestCase):
    def test_normalizes_valid_postal_code(self) -> None:
        self.assertEqual(validate_postal_code("12345-678"), "12345678")

    def test_rejects_placeholder(self) -> None:
        with self.assertRaises(ValueError):
            validate_postal_code("00000000")

    def test_extracts_kabum_shipping_identifiers(self) -> None:
        context = extract_kabum_shipping_context(KABUM_HTML)
        self.assertEqual(context.product_code, 167492)
        self.assertEqual(context.seller_id, "3294")
        self.assertEqual(context.offer_id, 374498)

    def test_chooses_cheapest_home_delivery(self) -> None:
        quote = parse_kabum_shipping_response(
            {
                "quotes": [
                    {
                        "deliveries": [
                            {"code": 32, "name": "Expressa", "price": 20.89,
                             "max_days": 3, "type": "CONVENTIONAL"},
                            {"code": 31, "name": "Padrão", "price": 25.75,
                             "max_days": 7, "type": "CONVENTIONAL"},
                            {"code": 99, "name": "Retirada", "price": 0,
                             "max_days": 1, "type": "TAKE_AWAY"},
                        ]
                    }
                ]
            }
        )
        self.assertEqual(quote.price, Decimal("20.89"))
        self.assertEqual(quote.delivery_max_days, 3)
        self.assertEqual(quote.service_name, "Expressa")

    def test_kabum_provider_builds_official_payload(self) -> None:
        captured: dict[str, object] = {}

        def poster(url: str, payload: dict[str, object], **kwargs: object) -> dict:
            captured.update({"url": url, "payload": payload, **kwargs})
            return {
                "quotes": [{"deliveries": [
                    {"code": 32, "name": "Expressa", "price": 20.89,
                     "max_days": 3, "type": "CONVENTIONAL"}
                ]}]
            }

        product = Product("SSD001", True, "SSD", "CT1000BX500SSD1",
                          Decimal("900"), "PIX")
        link = ProductLink("SSD001", "KaBuM", "https://www.kabum.com.br/produto/167492/x",
                           "TEITEC INFORMÁTICA", "CT1000BX500SSD1")
        quote = KabumShippingProvider(poster=poster).quote_from_html(
            product, link, "01001-000", KABUM_HTML
        )

        payload = captured["payload"]
        self.assertEqual(payload["zip_code"], "01001000")
        self.assertEqual(payload["sellers"][0]["seller_id"], "3294")
        self.assertEqual(payload["sellers"][0]["products"][0]["offer_id"], 374498)
        self.assertEqual(len(payload["client"]["session"]), 32)
        self.assertEqual(quote.price, Decimal("20.89"))

    def test_rejects_empty_shipping_response(self) -> None:
        with self.assertRaises(ShippingNotAvailable):
            parse_kabum_shipping_response({"quotes": []})

    def test_parses_free_amazon_delivery_date(self) -> None:
        html = (
            '<span data-csa-c-delivery-price="GRÁTIS" '
            'data-csa-c-delivery-time="Sexta-feira, 28 de Agosto"></span>'
        )
        quote = parse_amazon_shipping_html(
            html, observed_on=date(2026, 8, 26)
        )
        self.assertEqual(quote.price, Decimal("0.00"))
        self.assertEqual(quote.delivery_max_days, 2)

    def test_amazon_provider_uses_isolated_location_session(self) -> None:
        calls: list[dict[str, object]] = []
        product_gets = 0

        def requester(_opener: object, request: object, **_kwargs: object) -> str:
            nonlocal product_gets
            calls.append(
                {
                    "url": request.full_url,
                    "method": request.get_method(),
                    "data": request.data,
                    "headers": dict(request.headers),
                }
            )
            if "get-rendered-address-selections" in request.full_url:
                return '<script>CSRF_TOKEN : "temporary-token"</script>'
            if "address-change" in request.full_url:
                return '{"sembuUpdated":true,"isTransitOutOfAis":false}'
            product_gets += 1
            if product_gets == 1:
                return (
                    '<div data-a-modal=\'{"ajaxHeaders":'
                    '{"anti-csrftoken-a2z":"seed-token"}}\'></div>'
                )
            return (
                '<span data-csa-c-delivery-price="R$ 18,09" '
                'data-csa-c-delivery-time="29 de agosto"></span>'
            )

        product = Product("SSD001", True, "SSD", "CT1000BX500SSD1",
                          Decimal("900"), "PIX")
        link = ProductLink("SSD001", "Amazon", "https://www.amazon.com.br/dp/B07YD579WM",
                           "BPS Oficial", "CT1000BX500SSD1")
        provider = AmazonShippingProvider(
            requester=requester,
            opener_factory=lambda: object(),
        )
        quote = provider.quote_from_html(product, link, "01001-000", "")

        change_call = next(call for call in calls if "address-change" in call["url"])
        payload = json.loads(change_call["data"].decode("utf-8"))
        self.assertEqual(payload["zipCode"], "01001000")
        self.assertEqual(payload["locationType"], "LOCATION_INPUT")
        self.assertIn("Anti-csrftoken-a2z", change_call["headers"])
        self.assertEqual(quote.price, Decimal("18.09"))


if __name__ == "__main__":
    unittest.main()
