from __future__ import annotations

import re

from app.collectors.common import OfferParseError, ParsedOffer, parse_brl, text_content


PARSER_VERSION = "amazon-v1"


def _match(html: str, pattern: str) -> str | None:
    result = re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL)
    return text_content(result.group(1)) if result else None


def parse_amazon_html(html: str) -> ParsedOffer:
    title = _match(html, r"id=[\"']productTitle[\"'][^>]*>(.*?)</(?:span|h1)>")
    if not title:
        raise OfferParseError("A Amazon nao retornou uma pagina de produto valida")

    price_text = _match(
        html,
        r"id=[\"']corePrice[^\"']*[\"'][\s\S]{0,18000}?"
        r"class=[\"'][^\"']*a-offscreen[^\"']*[\"'][^>]*>(R\$\s*[\d.]+,\d{2})",
    )
    if price_text is None:
        price_text = _match(
            html,
            r"id=[\"'](?:price_inside_buybox|priceblock_(?:ourprice|dealprice))[\"']"
            r"[^>]*>(R\$\s*[\d.]+,\d{2})",
        )

    seller = _match(
        html,
        r"id=[\"']sellerProfileTriggerId[\"'][^>]*>(.*?)</a>",
    )
    if not seller:
        seller = _match(
            html,
            r"id=[\"']merchantInfoFeature_feature_div[\"'][\s\S]{0,5000}?"
            r"class=[\"'][^\"']*offer-display-feature-text-message[^\"']*[\"']"
            r"[^>]*>(.*?)</(?:span|a)>",
        )
    seller = seller or "Vendedor nao identificado"

    availability = _match(
        html,
        r"id=[\"']availability[\"'][^>]*>([\s\S]{0,1200}?)</div>",
    ) or ""
    unavailable = re.search(
        r"indispon[ií]vel|temporariamente fora de estoque|nao esta disponivel|não está disponível",
        availability,
        flags=re.IGNORECASE,
    )
    in_stock = not bool(unavailable) and (
        bool(re.search(r"em estoque|somente \d+ em estoque", availability, re.IGNORECASE))
        or price_text is not None
    )

    return ParsedOffer(
        title=title,
        seller=seller,
        product_price=parse_brl(price_text),
        in_stock=in_stock,
        supports_pix=bool(re.search(r"\bpix\b", html, re.IGNORECASE)),
        parser_version=PARSER_VERSION,
    )
