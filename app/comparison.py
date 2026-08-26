from __future__ import annotations

from collections.abc import Iterable

from app.models import OfferObservation


def comparable_offers(
    observations: Iterable[OfferObservation],
    product_id: str | None = None,
    *,
    require_shipping: bool = True,
) -> tuple[OfferObservation, ...]:
    return tuple(
        observation
        for observation in observations
        if (product_id is None or observation.product_id == product_id)
        and observation.status == "OK"
        and observation.in_stock
        and observation.product_price is not None
        and (not require_shipping or observation.total_price is not None)
    )


def choose_best_offer(
    observations: Iterable[OfferObservation],
    product_id: str | None = None,
    *,
    require_shipping: bool = True,
) -> OfferObservation | None:
    candidates = comparable_offers(
        observations, product_id, require_shipping=require_shipping
    )
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda offer: (
            offer.total_price if require_shipping else offer.product_price,
            offer.delivery_max_days if offer.delivery_max_days is not None else 10**9,
            offer.store.casefold(),
        ),
    )
