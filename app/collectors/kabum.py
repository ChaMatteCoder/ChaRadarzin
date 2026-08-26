from __future__ import annotations

import json
import re

from app.collectors.common import OfferParseError, ParsedOffer, parse_brl


PARSER_VERSION = "kabum-next-v1"


def parse_kabum_html(html: str) -> ParsedOffer:
    match = re.search(
        r"<script[^>]+id=[\"']__NEXT_DATA__[\"'][^>]*>(.*?)</script>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        raise OfferParseError("A KaBuM nao retornou os dados estruturados do produto")
    try:
        data = json.loads(match.group(1))
        product = data["props"]["pageProps"]["product"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise OfferParseError("Estrutura de produto inesperada na KaBuM") from exc

    prices = product.get("prices") or {}
    price = prices.get("priceWithDiscount")
    if price in (None, 0, "0"):
        price = prices.get("price") or product.get("price")
    title = str(product.get("title") or "").strip()
    if not title:
        raise OfferParseError("Titulo do produto ausente na KaBuM")

    return ParsedOffer(
        title=title,
        seller=str(
            product.get("sellerName")
            or (product.get("marketplace") or {}).get("sellerName")
            or "Vendedor nao identificado"
        ).strip(),
        product_price=parse_brl(price),
        in_stock=bool(product.get("available", (product.get("flags") or {}).get("isAvailable"))),
        supports_pix=bool(re.search(r"\bpix\b", html, re.IGNORECASE)),
        parser_version=PARSER_VERSION,
    )
