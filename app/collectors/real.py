from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from urllib.parse import urlsplit

from app.collectors.amazon import parse_amazon_html
from app.collectors.common import OfferParseError, ParsedOffer, compact, normalize
from app.collectors.http import FetchError, canonicalize_product_url, fetch_html
from app.collectors.kabum import parse_kabum_html
from app.models import Catalog, OfferObservation, Product, ProductLink
from app.shipping import ShippingNotAvailable, ShippingQuote, quote_shipping


Fetcher = Callable[..., str]
ShippingQuoter = Callable[..., ShippingQuote]


def _known_store_from_url(url: str) -> str | None:
    hostname = (urlsplit(url).hostname or "").casefold()
    if hostname == "amazon.com.br" or hostname.endswith(".amazon.com.br"):
        return "amazon"
    if hostname == "kabum.com.br" or hostname.endswith(".kabum.com.br"):
        return "kabum"
    return None


def _declared_store(store: str) -> str | None:
    value = normalize(store)
    if "amazon" in value:
        return "amazon"
    if "kabum" in value:
        return "kabum"
    return None


def _variant_matches(variant: str, title: str) -> bool:
    normalized_title = normalize(title)
    compact_title = compact(title)
    for raw_part in variant.split("/"):
        part = raw_part.strip()
        if not part:
            continue
        compact_part = compact(part)
        if compact_part and compact_part in compact_title:
            continue
        normalized_part = normalize(part)
        if "polegada" in normalized_part:
            number = re.search(r"\d+(?:[.,]\d+)?", normalized_part)
            if number and number.group(0).replace(",", ".") in normalized_title.replace(",", "."):
                continue
        tokens = [token for token in re.findall(r"[a-z0-9]+", normalized_part) if len(token) >= 2]
        if tokens and all(token in normalized_title for token in tokens):
            continue
        return False
    return True


def _seller_matches(expected: str, actual: str) -> bool:
    expected_value = normalize(expected)
    actual_value = normalize(actual)
    return bool(expected_value and actual_value) and (
        expected_value == actual_value
        or expected_value in actual_value
        or actual_value in expected_value
    )


def _observation(
    product: Product,
    link: ProductLink,
    timestamp: datetime,
    *,
    status: str,
    parser_version: str,
    parsed: ParsedOffer | None = None,
    shipping_quote: ShippingQuote | None = None,
    error_message: str | None = None,
) -> OfferObservation:
    return OfferObservation(
        observed_at=timestamp,
        product_id=product.product_id,
        store=link.store,
        seller=parsed.seller if parsed else "",
        payment_method=product.payment_method,
        product_price=parsed.product_price if parsed else None,
        shipping_price=shipping_quote.price if shipping_quote else None,
        delivery_min_days=(
            shipping_quote.delivery_min_days if shipping_quote else None
        ),
        delivery_max_days=(
            shipping_quote.delivery_max_days if shipping_quote else None
        ),
        in_stock=parsed.in_stock if parsed else False,
        url=canonicalize_product_url(link.url),
        status=status,
        parser_version=parser_version,
        error_message=error_message,
    )


def _collect_one(
    product: Product,
    link: ProductLink,
    timestamp: datetime,
    *,
    timeout: float,
    retries: int,
    fetcher: Fetcher,
    destination_postal_code: str | None,
    shipping_quoter: ShippingQuoter,
) -> OfferObservation:
    declared_store = _declared_store(link.store)
    url_store = _known_store_from_url(link.url)
    if url_store is None:
        return _observation(
            product,
            link,
            timestamp,
            status="UNSUPPORTED_STORE",
            parser_version="router-v1",
            error_message="Dominio sem coletor na Etapa 2",
        )
    if declared_store != url_store:
        return _observation(
            product,
            link,
            timestamp,
            status="STORE_MISMATCH",
            parser_version="router-v1",
            error_message=f"Loja declarada '{link.store}' nao corresponde ao dominio do link",
        )

    try:
        html = fetcher(link.url, timeout=timeout, retries=retries)
        parsed = parse_amazon_html(html) if url_store == "amazon" else parse_kabum_html(html)
    except FetchError as exc:
        return _observation(
            product,
            link,
            timestamp,
            status="FETCH_ERROR",
            parser_version=f"{url_store}-fetch-v1",
            error_message=str(exc),
        )
    except (OfferParseError, ValueError, KeyError, TypeError) as exc:
        return _observation(
            product,
            link,
            timestamp,
            status="PARSE_ERROR",
            parser_version=f"{url_store}-unknown",
            error_message=str(exc),
        )

    if compact(product.exact_model) not in compact(parsed.title):
        return _observation(
            product,
            link,
            timestamp,
            status="MODEL_MISMATCH",
            parser_version=parsed.parser_version,
            parsed=parsed,
            error_message=(
                f"Modelo esperado '{product.exact_model}' nao aparece no titulo: "
                f"{parsed.title[:180]}"
            ),
        )
    if not _variant_matches(link.variant, parsed.title):
        return _observation(
            product,
            link,
            timestamp,
            status="VARIANT_MISMATCH",
            parser_version=parsed.parser_version,
            parsed=parsed,
            error_message=f"Variante esperada '{link.variant}' nao confere com o titulo",
        )
    if not _seller_matches(link.expected_seller, parsed.seller):
        return _observation(
            product,
            link,
            timestamp,
            status="SELLER_MISMATCH",
            parser_version=parsed.parser_version,
            parsed=parsed,
            error_message=(
                f"Vendedor esperado '{link.expected_seller}', encontrado '{parsed.seller}'"
            ),
        )
    if not parsed.in_stock:
        return _observation(
            product,
            link,
            timestamp,
            status="OUT_OF_STOCK",
            parser_version=parsed.parser_version,
            parsed=parsed,
            error_message="Produto sem estoque",
        )
    if parsed.product_price is None:
        return _observation(
            product,
            link,
            timestamp,
            status="PARSE_ERROR",
            parser_version=parsed.parser_version,
            parsed=parsed,
            error_message="Preco principal nao identificado",
        )
    if product.payment_method == "PIX" and not parsed.supports_pix:
        return _observation(
            product,
            link,
            timestamp,
            status="PAYMENT_MISMATCH",
            parser_version=parsed.parser_version,
            parsed=parsed,
            error_message="A pagina nao confirmou preco ou pagamento via PIX",
        )

    shipping_quote = None
    if destination_postal_code is not None:
        try:
            shipping_quote = shipping_quoter(
                product,
                link,
                destination_postal_code,
                html,
                timeout=timeout,
                retries=retries,
            )
        except (ShippingNotAvailable, ValueError, KeyError, TypeError) as exc:
            return _observation(
                product,
                link,
                timestamp,
                status="SHIPPING_UNAVAILABLE",
                parser_version=f"{parsed.parser_version}+shipping-v1",
                parsed=parsed,
                error_message=str(exc),
            )

    return _observation(
        product,
        link,
        timestamp,
        status="OK",
        parser_version=(
            f"{parsed.parser_version}+shipping-v1" if shipping_quote else parsed.parser_version
        ),
        parsed=parsed,
        shipping_quote=shipping_quote,
    )


def collect_real_observations(
    catalog: Catalog,
    observed_at: datetime | None = None,
    *,
    timeout: float = 20.0,
    retries: int = 1,
    fetcher: Fetcher = fetch_html,
    destination_postal_code: str | None = None,
    shipping_quoter: ShippingQuoter = quote_shipping,
) -> tuple[OfferObservation, ...]:
    timestamp = observed_at or datetime.now().astimezone()
    products = {product.product_id: product for product in catalog.active_products}
    observations: list[OfferObservation] = []
    for link in catalog.links:
        product = products.get(link.product_id)
        if product is None:
            continue
        observations.append(
            _collect_one(
                product,
                link,
                timestamp,
                timeout=timeout,
                retries=retries,
                fetcher=fetcher,
                destination_postal_code=destination_postal_code,
                shipping_quoter=shipping_quoter,
            )
        )
    return tuple(observations)
