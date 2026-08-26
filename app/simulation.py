from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from app.models import Catalog, OfferObservation


CENT = Decimal("0.01")


def generate_simulated_observations(
    catalog: Catalog, observed_at: datetime | None = None
) -> tuple[OfferObservation, ...]:
    timestamp = observed_at or datetime.now().astimezone()
    observations: list[OfferObservation] = []

    for product in catalog.active_products:
        for index, link in enumerate(catalog.links_for(product.product_id)):
            price_factor = Decimal("0.94") + Decimal(index) * Decimal("0.035")
            product_price = (product.target_price * price_factor).quantize(CENT, ROUND_HALF_UP)
            shipping = (Decimal("18.90") + Decimal(index) * Decimal("7.50")).quantize(
                CENT, ROUND_HALF_UP
            )
            observations.append(
                OfferObservation(
                    observed_at=timestamp,
                    product_id=product.product_id,
                    store=link.store,
                    seller=link.expected_seller,
                    payment_method=product.payment_method,
                    product_price=product_price,
                    shipping_price=shipping,
                    delivery_min_days=2 + index,
                    delivery_max_days=5 + index,
                    in_stock=True,
                    url=link.url,
                    status="OK",
                    parser_version="simulation-v1",
                )
            )
    return tuple(observations)
