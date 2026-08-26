from __future__ import annotations

import html as html_module
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


class OfferParseError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ParsedOffer:
    title: str
    seller: str
    product_price: Decimal | None
    in_stock: bool
    supports_pix: bool
    parser_version: str


def text_content(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", value)
    return " ".join(html_module.unescape(without_tags).split())


def normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(without_accents.casefold().split())


def compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", normalize(value))


def parse_brl(value: str | int | float | Decimal | None) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value.quantize(Decimal("0.01"))
    if isinstance(value, (int, float)):
        return Decimal(str(value)).quantize(Decimal("0.01"))
    cleaned = text_content(str(value)).replace("R$", "").replace(" ", "")
    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        return Decimal(cleaned).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise OfferParseError(f"Preco invalido: {value!r}") from exc
