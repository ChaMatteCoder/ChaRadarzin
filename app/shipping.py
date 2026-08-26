from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Callable
from datetime import date, datetime
from dataclasses import dataclass
from decimal import Decimal
from html import unescape
from http.cookiejar import CookieJar
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

from app.models import Product, ProductLink


KABUM_SHIPPING_URL = (
    "https://servicespub.prod.api.aws.grupokabum.com.br/shipping/v4/quotation"
)
AMAZON_LOCATION_SELECTIONS_URL = (
    "https://www.amazon.com.br/portal-migration/hz/glow/get-rendered-address-selections"
    "?deviceType=desktop&pageType=Detail&storeContext=computers&actionSource=desktop-modal"
)
AMAZON_ADDRESS_CHANGE_URL = (
    "https://www.amazon.com.br/portal-migration/hz/glow/address-change?actionSource=glow"
)
SHIPPING_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)


@dataclass(frozen=True, slots=True)
class ShippingQuote:
    price: Decimal
    delivery_min_days: int | None
    delivery_max_days: int | None
    service_name: str = ""
    service_code: str = ""


class ShippingProvider(Protocol):
    def quote(
        self, product: Product, link: ProductLink, destination_postal_code: str
    ) -> ShippingQuote:
        ...


class ShippingNotAvailable(RuntimeError):
    pass


def validate_postal_code(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if len(digits) != 8 or digits == "00000000":
        raise ValueError("CEP_ENTREGA deve conter 8 digitos e nao pode ser 00000000")
    return digits


@dataclass(frozen=True, slots=True)
class KabumShippingContext:
    product_code: int
    seller_name: str
    seller_id: str
    offer_id: int


def _kabum_product_payload(html: str) -> dict[str, Any]:
    match = re.search(
        r"<script[^>]+id=[\"']__NEXT_DATA__[\"'][^>]*>(.*?)</script>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        raise ShippingNotAvailable("Dados de frete ausentes na pagina da KaBuM")
    try:
        payload = json.loads(match.group(1))
        product = payload["props"]["pageProps"]["product"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ShippingNotAvailable("Estrutura de frete inesperada na KaBuM") from exc
    if not isinstance(product, dict):
        raise ShippingNotAvailable("Produto invalido na resposta da KaBuM")
    return product


def extract_kabum_shipping_context(html: str) -> KabumShippingContext:
    product = _kabum_product_payload(html)
    marketplace = product.get("marketplace") or {}
    try:
        product_code = int(product.get("id") or product.get("code"))
        seller_name = str(
            product.get("sellerName") or marketplace.get("sellerName") or ""
        ).strip()
        seller_id = str(
            product.get("sellerId") or marketplace.get("sellerId") or ""
        ).strip()
        offer_id = int(
            product.get("offerIdMarketplace")
            or marketplace.get("offerIdMarketplace")
            or marketplace.get("offerId")
            or 0
        )
    except (TypeError, ValueError) as exc:
        raise ShippingNotAvailable("Identificadores de frete invalidos na KaBuM") from exc
    if not product_code or not seller_name or not seller_id:
        raise ShippingNotAvailable("Identificadores de frete incompletos na KaBuM")
    return KabumShippingContext(product_code, seller_name, seller_id, offer_id)


def parse_kabum_shipping_response(payload: dict[str, Any]) -> ShippingQuote:
    candidates: list[ShippingQuote] = []
    for seller_quote in payload.get("quotes") or []:
        for delivery in seller_quote.get("deliveries") or []:
            if str(delivery.get("type") or "").upper() == "TAKE_AWAY":
                continue
            try:
                price = Decimal(str(delivery["price"])).quantize(Decimal("0.01"))
                max_days = int(delivery["max_days"])
            except (KeyError, TypeError, ValueError, ArithmeticError):
                continue
            candidates.append(
                ShippingQuote(
                    price=price,
                    delivery_min_days=None,
                    delivery_max_days=max_days,
                    service_name=str(delivery.get("name") or "").strip(),
                    service_code=str(delivery.get("code") or "").strip(),
                )
            )
    if not candidates:
        raise ShippingNotAvailable("A KaBuM nao retornou uma modalidade de entrega")
    return min(
        candidates,
        key=lambda quote: (
            quote.price,
            quote.delivery_max_days if quote.delivery_max_days is not None else 10**9,
        ),
    )


JsonPoster = Callable[..., dict[str, Any]]
AmazonRequester = Callable[..., str]


def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    timeout: float,
    retries: int,
) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Referer": "https://www.kabum.com.br/",
            "User-Agent": SHIPPING_USER_AGENT,
        },
    )
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
            if not isinstance(result, dict):
                raise ShippingNotAvailable("Resposta de frete inesperada na KaBuM")
            return result
        except ShippingNotAvailable:
            raise
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(0.6 * (attempt + 1))
    raise ShippingNotAvailable(f"Falha ao consultar frete na KaBuM: {last_error}")


class KabumShippingProvider:
    def __init__(
        self,
        *,
        timeout: float = 20.0,
        retries: int = 1,
        poster: JsonPoster = _post_json,
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self.poster = poster

    def quote_from_html(
        self,
        product: Product,
        link: ProductLink,
        destination_postal_code: str,
        html: str,
    ) -> ShippingQuote:
        del product, link
        postal_code = validate_postal_code(destination_postal_code)
        context = extract_kabum_shipping_context(html)
        request_payload = {
            "zip_code": postal_code,
            "sellers": [
                {
                    "seller_name": context.seller_name,
                    "seller_id": context.seller_id,
                    "products": [
                        {
                            "code": context.product_code,
                            "quantity": 1,
                            "offer_id": context.offer_id,
                        }
                    ],
                }
            ],
            "client": {
                "id": "",
                "session": uuid.uuid4().hex,
                "type": "F",
                "is_prime": False,
            },
            "store": 1,
            "origin": "product",
        }
        response = self.poster(
            KABUM_SHIPPING_URL,
            request_payload,
            timeout=self.timeout,
            retries=self.retries,
        )
        return parse_kabum_shipping_response(response)

    def quote(
        self, product: Product, link: ProductLink, destination_postal_code: str
    ) -> ShippingQuote:
        del product, destination_postal_code
        raise ShippingNotAvailable(
            f"A cotacao da KaBuM precisa do HTML ja validado: {link.url}"
        )


class AmazonShippingProvider:
    def __init__(
        self,
        *,
        timeout: float = 20.0,
        retries: int = 1,
        requester: AmazonRequester | None = None,
        opener_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self.requester = requester or _amazon_request_text
        self.opener_factory = opener_factory or _amazon_opener

    def quote_from_html(
        self,
        product: Product,
        link: ProductLink,
        destination_postal_code: str,
        html: str,
    ) -> ShippingQuote:
        del product, html
        postal_code = validate_postal_code(destination_postal_code)
        hostname = (urlsplit(link.url).hostname or "").casefold()
        if hostname != "amazon.com.br" and not hostname.endswith(".amazon.com.br"):
            raise ShippingNotAvailable("Link de frete nao pertence a Amazon Brasil")

        opener = self.opener_factory()
        product_url = link.url
        seed_request = Request(product_url, headers=_amazon_headers(product_url))
        seed_html = self.requester(
            opener, seed_request, timeout=self.timeout, retries=self.retries
        )
        seed_token_match = re.search(
            r'ajaxHeaders"\s*:\s*\{\s*"anti-csrftoken-a2z"\s*:\s*"([^"]+)',
            unescape(seed_html),
            flags=re.IGNORECASE,
        )
        if not seed_token_match:
            raise ShippingNotAvailable("Token inicial de localizacao ausente na Amazon")

        modal_request = Request(
            AMAZON_LOCATION_SELECTIONS_URL,
            headers={
                **_amazon_headers(product_url, ajax=True),
                "anti-csrftoken-a2z": seed_token_match.group(1),
            },
        )
        modal_html = self.requester(
            opener, modal_request, timeout=self.timeout, retries=self.retries
        )
        token_match = re.search(
            r"CSRF_TOKEN\s*:\s*[\"']([^\"']+)", modal_html, flags=re.IGNORECASE
        )
        if not token_match:
            raise ShippingNotAvailable("Token de localizacao ausente na Amazon")

        payload = {
            "locationType": "LOCATION_INPUT",
            "zipCode": postal_code,
            "deviceType": "web",
            "storeContext": "computers",
            "pageType": "Detail",
            "actionSource": "glow",
        }
        change_request = Request(
            AMAZON_ADDRESS_CHANGE_URL,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                **_amazon_headers(product_url, ajax=True),
                "Content-Type": "application/json",
                "anti-csrftoken-a2z": unescape(token_match.group(1)),
            },
        )
        response_text = self.requester(
            opener, change_request, timeout=self.timeout, retries=self.retries
        )
        try:
            change_response = json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise ShippingNotAvailable(
                "Resposta de localizacao invalida na Amazon"
            ) from exc
        if not isinstance(change_response, dict):
            raise ShippingNotAvailable("Resposta de localizacao inesperada na Amazon")

        final_request = Request(product_url, headers=_amazon_headers(product_url))
        final_html = self.requester(
            opener, final_request, timeout=self.timeout, retries=self.retries
        )
        return parse_amazon_shipping_html(final_html)

    def quote(
        self, product: Product, link: ProductLink, destination_postal_code: str
    ) -> ShippingQuote:
        return self.quote_from_html(product, link, destination_postal_code, "")


def shipping_provider_for(store: str, *, timeout: float, retries: int) -> Any:
    normalized = store.casefold()
    if "kabum" in normalized:
        return KabumShippingProvider(timeout=timeout, retries=retries)
    if "amazon" in normalized:
        return AmazonShippingProvider(timeout=timeout, retries=retries)
    return PendingShippingProvider()


def quote_shipping(
    product: Product,
    link: ProductLink,
    destination_postal_code: str,
    html: str,
    *,
    timeout: float,
    retries: int,
) -> ShippingQuote:
    provider = shipping_provider_for(link.store, timeout=timeout, retries=retries)
    quote_from_html = getattr(provider, "quote_from_html", None)
    if quote_from_html is None:
        return provider.quote(product, link, destination_postal_code)
    return quote_from_html(product, link, destination_postal_code, html)


class PendingShippingProvider:
    def quote(
        self, product: Product, link: ProductLink, destination_postal_code: str
    ) -> ShippingQuote:
        validate_postal_code(destination_postal_code)
        raise ShippingNotAvailable(
            f"Cotacao de frete nao implementada para {link.store}"
        )


def _amazon_headers(referer: str, *, ajax: bool = False) -> dict[str, str]:
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/json",
        "Accept-Language": "pt-BR,pt;q=0.9",
        "Referer": referer,
        "User-Agent": SHIPPING_USER_AGENT,
    }
    if ajax:
        headers["X-Requested-With"] = "XMLHttpRequest"
    return headers


def _amazon_opener() -> Any:
    return build_opener(HTTPCookieProcessor(CookieJar()))


def _amazon_request_text(
    opener: Any,
    request: Request,
    *,
    timeout: float,
    retries: int,
) -> str:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with opener.open(request, timeout=timeout) as response:
                payload = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(0.6 * (attempt + 1))
    raise ShippingNotAvailable(f"Falha ao consultar frete na Amazon: {last_error}")


def _delivery_days(value: str, *, today: date) -> int | None:
    normalized = unescape(value).casefold()
    if "hoje" in normalized:
        return 0
    if "amanha" in normalized or "amanhã" in normalized:
        return 1
    months = {
        "jan": 1,
        "janeiro": 1,
        "fev": 2,
        "fevereiro": 2,
        "mar": 3,
        "marco": 3,
        "março": 3,
        "abr": 4,
        "abril": 4,
        "mai": 5,
        "maio": 5,
        "jun": 6,
        "junho": 6,
        "jul": 7,
        "julho": 7,
        "ago": 8,
        "agosto": 8,
        "set": 9,
        "setembro": 9,
        "out": 10,
        "outubro": 10,
        "nov": 11,
        "novembro": 11,
        "dez": 12,
        "dezembro": 12,
    }
    month_match = re.search(
        r"\b(jan(?:eiro)?|fev(?:ereiro)?|mar(?:co|ço)?|abr(?:il)?|mai(?:o)?|"
        r"jun(?:ho)?|jul(?:ho)?|ago(?:sto)?|set(?:embro)?|out(?:ubro)?|"
        r"nov(?:embro)?|dez(?:embro)?)\b",
        normalized,
    )
    if not month_match:
        return None
    before_month = normalized[: month_match.start()]
    days = [int(value) for value in re.findall(r"\b(\d{1,2})\b", before_month)]
    if not days:
        return None
    delivery_day = days[-1]
    delivery_month = months[month_match.group(1)]
    year = today.year
    try:
        delivery_date = date(year, delivery_month, delivery_day)
    except ValueError:
        return None
    if delivery_date < today:
        delivery_date = date(year + 1, delivery_month, delivery_day)
    return (delivery_date - today).days


def parse_amazon_shipping_html(
    html: str, *, observed_on: date | None = None
) -> ShippingQuote:
    today = observed_on or datetime.now().astimezone().date()
    tags = re.findall(
        r"<[^>]+data-csa-c-delivery-price=[\"'][^\"']+[\"'][^>]*>",
        html,
        flags=re.IGNORECASE,
    )
    for tag in tags:
        price_match = re.search(
            r"data-csa-c-delivery-price=[\"']([^\"']+)", tag, flags=re.IGNORECASE
        )
        time_match = re.search(
            r"data-csa-c-delivery-time=[\"']([^\"']+)", tag, flags=re.IGNORECASE
        )
        if not price_match:
            continue
        price_text = unescape(price_match.group(1)).strip()
        if re.search(r"gr[aá]tis|gratuita", price_text, flags=re.IGNORECASE):
            price = Decimal("0.00")
        else:
            number = re.search(r"R\$\s*([\d.]+,\d{2})", price_text)
            if not number:
                continue
            price = Decimal(number.group(1).replace(".", "").replace(",", "."))
        delivery_text = unescape(time_match.group(1)) if time_match else ""
        max_days = _delivery_days(delivery_text, today=today)
        return ShippingQuote(
            price=price,
            delivery_min_days=None,
            delivery_max_days=max_days,
            service_name="Entrega Amazon",
            service_code="AMAZON_DELIVERY",
        )
    raise ShippingNotAvailable("Preco de frete nao identificado na pagina da Amazon")
