from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlsplit

from monitoring.models import SupportedStore


AMAZON_HOSTS = {"amazon.com.br", "www.amazon.com.br"}
KABUM_HOSTS = {"kabum.com.br", "www.kabum.com.br"}
AMAZON_PRODUCT_PATH = re.compile(
    r"/(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?]|$)",
    re.IGNORECASE,
)
KABUM_PRODUCT_PATH = re.compile(
    r"^/produto/(\d+)(?:/[^?#]*)?$",
    re.IGNORECASE,
)


class ExactProductUrlError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ExactProductUrl:
    submitted_url: str
    canonical_url: str
    hostname: str
    store: str


def validate_exact_product_url(value: str) -> ExactProductUrl:
    submitted = value.strip()
    if not submitted or len(submitted) > 2048:
        raise ExactProductUrlError("INVALID_URL", "Informe um link de produto valido.")
    if any(character.isspace() or ord(character) < 32 for character in submitted):
        raise ExactProductUrlError("INVALID_URL", "O link contem caracteres invalidos.")
    try:
        parts = urlsplit(submitted)
        port = parts.port
    except ValueError as exc:
        raise ExactProductUrlError("INVALID_URL", "O link informado e invalido.") from exc
    hostname = (parts.hostname or "").casefold().rstrip(".")
    if parts.scheme.casefold() != "https":
        raise ExactProductUrlError("HTTPS_REQUIRED", "Use o link HTTPS completo do produto.")
    if not hostname or parts.username or parts.password or port not in (None, 443):
        raise ExactProductUrlError("UNSAFE_AUTHORITY", "O host ou a porta do link nao e permitido.")
    if parts.fragment:
        raise ExactProductUrlError("FRAGMENT_NOT_ALLOWED", "Remova o trecho apos # do link.")

    if hostname in AMAZON_HOSTS:
        match = AMAZON_PRODUCT_PATH.search(parts.path)
        if match is None:
            raise ExactProductUrlError(
                "NOT_A_PRODUCT_URL",
                "Cole o link exato de um produto da Amazon Brasil.",
            )
        query = parse_qs(parts.query, keep_blank_values=False)
        kept_query = urlencode({"th": query["th"][0]}) if query.get("th") else ""
        canonical = f"https://www.amazon.com.br/dp/{match.group(1).upper()}"
        if kept_query:
            canonical += f"?{kept_query}"
        return ExactProductUrl(submitted, canonical, "www.amazon.com.br", SupportedStore.AMAZON)

    if hostname in KABUM_HOSTS:
        match = KABUM_PRODUCT_PATH.fullmatch(parts.path)
        if match is None:
            raise ExactProductUrlError(
                "NOT_A_PRODUCT_URL",
                "Cole o link exato de um produto da KaBuM.",
            )
        clean_path = re.sub(r"/{2,}", "/", parts.path).rstrip("/")
        query = parse_qs(parts.query, keep_blank_values=False)
        kept_query = ""
        if query.get("seller_offer_id"):
            seller_offer_id = query["seller_offer_id"][0][:200]
            kept_query = urlencode({"seller_offer_id": seller_offer_id})
        canonical = f"https://www.kabum.com.br{clean_path}"
        if kept_query:
            canonical += f"?{kept_query}"
        return ExactProductUrl(submitted, canonical, "www.kabum.com.br", SupportedStore.KABUM)

    raise ExactProductUrlError(
        "UNSUPPORTED_STORE",
        "Nesta versao, use um link exato da Amazon Brasil ou da KaBuM.",
    )


def ensure_public_dns(
    hostname: str,
    *,
    resolver=socket.getaddrinfo,
) -> tuple[str, ...]:
    try:
        answers = resolver(hostname, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ExactProductUrlError("DNS_FAILED", "A loja nao pode ser localizada agora.") from exc
    addresses = sorted({str(answer[4][0]).split("%", 1)[0] for answer in answers})
    if not addresses:
        raise ExactProductUrlError("DNS_FAILED", "A loja nao pode ser localizada agora.")
    for raw_address in addresses:
        try:
            address = ipaddress.ip_address(raw_address)
        except ValueError as exc:
            raise ExactProductUrlError("DNS_INVALID", "A loja retornou um endereco invalido.") from exc
        if not address.is_global:
            raise ExactProductUrlError(
                "NON_PUBLIC_ADDRESS",
                "O destino do link nao e publico e foi bloqueado.",
            )
    return tuple(addresses)
