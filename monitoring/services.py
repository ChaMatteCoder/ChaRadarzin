from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from monitoring.models import MonitoredProduct, OfferSource, ProductStatus


@dataclass(frozen=True, slots=True)
class CollectionState:
    code: str
    label: str
    detail: str
    tone: str


def collection_state_for(
    product: MonitoredProduct,
    latest_status: str | None = None,
    active_source_count: int | None = None,
) -> CollectionState:
    if product.status == ProductStatus.ARCHIVED:
        return CollectionState("ARCHIVED", "Removido", "Fora do radar; histórico preservado.", "muted")
    if product.status == ProductStatus.PAUSED:
        return CollectionState("PAUSED", "Pausado", "Nenhuma nova coleta será solicitada.", "muted")
    if active_source_count == 0:
        return CollectionState("NO_SOURCES", "Sem fontes ativas", "Adicione ou reative um link exato.", "warning")
    if latest_status is None:
        return CollectionState(
            "AWAITING_BASELINE",
            "Aguardando primeira coleta",
            "A próxima coleta válida criará a baseline sem alerta de variação.",
            "pending",
        )
    if latest_status == "OK":
        return CollectionState("MONITORING", "Monitorando", "Última coleta válida.", "success")
    if latest_status == "OUT_OF_STOCK":
        return CollectionState("OUT_OF_STOCK", "Sem estoque", "A oferta foi validada, mas está indisponível.", "warning")
    return CollectionState("NEEDS_ATTENTION", "Requer atenção", "A última coleta não produziu oferta comparável.", "danger")


@transaction.atomic
def toggle_product_pause(product: MonitoredProduct) -> MonitoredProduct:
    locked = MonitoredProduct.objects.select_for_update().get(pk=product.pk)
    if locked.status == ProductStatus.ARCHIVED:
        return locked
    locked.status = (
        ProductStatus.ACTIVE if locked.status == ProductStatus.PAUSED else ProductStatus.PAUSED
    )
    locked.save(update_fields=["status", "updated_at"])
    return locked


@transaction.atomic
def archive_product(product: MonitoredProduct) -> MonitoredProduct:
    locked = MonitoredProduct.objects.select_for_update().get(pk=product.pk)
    locked.status = ProductStatus.ARCHIVED
    locked.save(update_fields=["status", "updated_at"])
    OfferSource.objects.filter(product=locked, tenant=locked.tenant).update(
        active=False,
        updated_at=timezone.now(),
    )
    return locked
