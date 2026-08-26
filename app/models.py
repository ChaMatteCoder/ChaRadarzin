from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Product:
    product_id: str
    active: bool
    name: str
    exact_model: str
    target_price: Decimal
    payment_method: str


@dataclass(frozen=True, slots=True)
class ProductLink:
    product_id: str
    store: str
    url: str
    expected_seller: str
    variant: str


@dataclass(frozen=True, slots=True)
class Catalog:
    products: tuple[Product, ...]
    links: tuple[ProductLink, ...]

    @property
    def active_products(self) -> tuple[Product, ...]:
        return tuple(product for product in self.products if product.active)

    def links_for(self, product_id: str) -> tuple[ProductLink, ...]:
        return tuple(link for link in self.links if link.product_id == product_id)


@dataclass(frozen=True, slots=True)
class OfferObservation:
    observed_at: datetime
    product_id: str
    store: str
    seller: str
    payment_method: str
    product_price: Decimal | None
    shipping_price: Decimal | None
    delivery_min_days: int | None
    delivery_max_days: int | None
    in_stock: bool
    url: str
    status: str
    parser_version: str
    error_message: str | None = None

    @property
    def total_price(self) -> Decimal | None:
        if self.product_price is None or self.shipping_price is None:
            return None
        return self.product_price + self.shipping_price


@dataclass(frozen=True, slots=True)
class ProductSummary:
    product: Product
    best_offer: OfferObservation | None
    previous_best_total: Decimal | None
    historical_low: Decimal | None
    include_shipping: bool = True

    @property
    def best_price(self) -> Decimal | None:
        if self.best_offer is None:
            return None
        if self.include_shipping:
            return self.best_offer.total_price
        return self.best_offer.product_price

    @property
    def daily_change(self) -> Decimal | None:
        current = self.best_price
        previous = self.previous_best_total
        if current is None or previous in (None, Decimal("0")):
            return None
        return (current - previous) / previous

    @property
    def situation(self) -> str:
        if self.best_price is None:
            return "Sem oferta comparavel"
        if self.historical_low == self.best_price:
            return "Menor preco historico"
        if self.product.target_price >= self.best_price:
            return "Abaixo do preco-alvo"
        return "Acima do preco-alvo"


@dataclass(frozen=True, slots=True)
class PriceHistoryPoint:
    observed_at: datetime
    price: Decimal
    store: str
    payment_method: str
