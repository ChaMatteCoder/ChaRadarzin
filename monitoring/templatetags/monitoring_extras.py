from decimal import Decimal
from urllib.parse import urlsplit

from django import template


register = template.Library()


@register.filter
def brl_cents(value):
    if value in (None, ""):
        return "—"
    amount = Decimal(value) / 100
    rendered = f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {rendered}"


@register.filter
def cep(value):
    raw = str(value or "")
    return f"{raw[:5]}-{raw[5:]}" if len(raw) == 8 else raw


@register.filter
def hostname(value):
    return urlsplit(str(value or "")).hostname or ""
