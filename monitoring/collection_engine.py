from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Callable

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone

from app.collectors.amazon import parse_amazon_html
from app.collectors.common import OfferParseError, ParsedOffer
from app.collectors.kabum import parse_kabum_html
from app.collectors.validation import model_matches, seller_matches, variant_matches
from app.models import Product, ProductLink
from app.shipping import ShippingNotAvailable, ShippingQuote, quote_shipping
from monitoring.models import (
    CollectionBatch,
    CollectionBatchStatus,
    CollectorError,
    MonitoredProduct,
    MonitoringRun,
    OfferSource,
    PriceObservation,
    ProductStatus,
    RunStatus,
    SharedOffer,
    SharedOfferObservation,
    SupportedStore,
)
from monitoring.alerts import evaluate_collection_alerts
from monitoring.preview import ProductPreviewError, fetch_preview_html
from monitoring.url_safety import ExactProductUrlError, validate_exact_product_url
from tenancy.models import DeliveryProfile


Fetcher = Callable[[str], str]
ShippingQuoter = Callable[..., ShippingQuote]
LOGGER = logging.getLogger(__name__)


class CollectionExecutionError(RuntimeError):
    def __init__(self, batch_id: str, *, retryable: bool) -> None:
        super().__init__("Falha inesperada durante a coleta compartilhada.")
        self.batch_id = batch_id
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class CollectionResult:
    batch_id: str
    status: str
    urls_planned: int
    urls_fetched: int
    sources_processed: int
    tenant_runs_count: int
    baselines_created: int
    alert_events_created: int = 0


@dataclass(frozen=True, slots=True)
class CollectedBase:
    observation: SharedOfferObservation
    html: str


class StoreRateLimiter:
    def __init__(
        self,
        requests_per_minute: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if requests_per_minute < 1:
            raise ValueError("requests_per_minute deve ser positivo")
        self.interval_seconds = 60.0 / requests_per_minute
        self.clock = clock
        self.sleeper = sleeper
        self._next_request_at: dict[str, float] = {}

    def wait(self, store: str) -> None:
        now = self.clock()
        delay = max(0.0, self._next_request_at.get(store, now) - now)
        if delay:
            self.sleeper(delay)
            now = self.clock()
        self._next_request_at[store] = now + self.interval_seconds


def _money_to_cents(value: Decimal | None) -> int | None:
    if value is None:
        return None
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError("Valor monetario invalido")
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _default_fetcher(url: str) -> str:
    return fetch_preview_html(
        url,
        timeout_seconds=settings.COLLECTION_TIMEOUT_SECONDS,
        max_bytes=settings.COLLECTION_MAX_BYTES,
    )


def _parse_offer(store: str, html: str) -> ParsedOffer:
    if store == SupportedStore.AMAZON:
        return parse_amazon_html(html)
    if store == SupportedStore.KABUM:
        return parse_kabum_html(html)
    raise OfferParseError("Loja sem parser compartilhado")


def _collect_base_offer(
    batch: CollectionBatch,
    shared_offer: SharedOffer,
    *,
    fetcher: Fetcher,
    clock: Callable[[], float],
) -> CollectedBase:
    started = clock()
    observed_at = timezone.now()
    html = ""
    fields = {
        "title": "",
        "seller": "",
        "product_price_cents": None,
        "in_stock": False,
        "supports_pix": False,
        "price_payment_method": "",
        "status": "FETCH_ERROR",
        "parser_version": "",
        "error_code": "FETCH_FAILED",
    }
    try:
        html = fetcher(shared_offer.canonical_url)
        parsed = _parse_offer(shared_offer.store, html)
    except ProductPreviewError as exc:
        fields["error_code"] = exc.code[:80]
    except OfferParseError:
        fields["status"] = "PARSE_ERROR"
        fields["error_code"] = "PARSE_FAILED"
    except (ValueError, KeyError, TypeError):
        fields["status"] = "PARSE_ERROR"
        fields["error_code"] = "PARSE_FAILED"
    else:
        try:
            product_price_cents = _money_to_cents(parsed.product_price)
        except ValueError:
            product_price_cents = None
        fields.update(
            {
                "title": parsed.title[:500],
                "seller": parsed.seller[:255],
                "product_price_cents": product_price_cents,
                "in_stock": parsed.in_stock,
                "supports_pix": parsed.supports_pix,
                "price_payment_method": parsed.price_payment_method[:16],
                "status": "OK",
                "parser_version": parsed.parser_version[:80],
                "error_code": "",
            }
        )
        if parsed.in_stock and not fields["product_price_cents"]:
            fields["status"] = "PARSE_ERROR"
            fields["error_code"] = "PRICE_INVALID"
    duration_ms = max(0, int((clock() - started) * 1000))
    observation = SharedOfferObservation.objects.create(
        batch=batch,
        shared_offer=shared_offer,
        observed_at=observed_at,
        duration_ms=duration_ms,
        **fields,
    )
    SharedOffer.objects.filter(pk=shared_offer.pk).update(
        last_collected_at=observed_at,
        last_status=observation.status,
        updated_at=timezone.now(),
    )
    return CollectedBase(observation=observation, html=html)


def _safe_status_message(status: str) -> str:
    messages = {
        "INVALID_URL": "O link cadastrado deixou de ser uma URL exata suportada.",
        "STORE_MISMATCH": "A loja declarada nao corresponde ao dominio da URL.",
        "FETCH_ERROR": "A loja nao respondeu com uma pagina de produto valida.",
        "PARSE_ERROR": "A estrutura da pagina nao pode ser interpretada.",
        "MODEL_MISMATCH": "O modelo exato nao aparece no titulo coletado.",
        "VARIANT_MISMATCH": "A variante esperada nao confere com o titulo coletado.",
        "SELLER_MISMATCH": "O vendedor encontrado difere do vendedor confirmado.",
        "PAYMENT_MISMATCH": "A forma de pagamento preferida nao foi confirmada.",
        "OUT_OF_STOCK": "A oferta esta sem estoque.",
        "PROFILE_INCOMPLETE": "Configure um CEP valido antes da coleta com frete.",
        "SHIPPING_UNAVAILABLE": "Nao foi possivel calcular o frete para este perfil.",
    }
    return messages.get(status, "A oferta nao produziu uma observacao comparavel.")


def _source_status(source: OfferSource, base: SharedOfferObservation) -> str:
    if base.status != "OK":
        return base.status
    product = source.product
    if not model_matches(product.exact_model, base.title):
        return "MODEL_MISMATCH"
    if not variant_matches(source.expected_variant or product.variant, base.title):
        return "VARIANT_MISMATCH"
    if not seller_matches(source.expected_seller, base.seller):
        return "SELLER_MISMATCH"
    if not base.in_stock:
        return "OUT_OF_STOCK"
    if base.product_price_cents is None:
        return "PARSE_ERROR"
    if product.preferred_payment_method == "PIX" and base.price_payment_method != "PIX":
        return "PAYMENT_MISMATCH"
    if product.preferred_payment_method != "PIX" and base.price_payment_method == "PIX":
        return "PAYMENT_MISMATCH"
    return "READY_FOR_SHIPPING"


def _legacy_product(product: MonitoredProduct) -> Product:
    target = Decimal(product.target_price_cents or 0) / Decimal(100)
    return Product(
        product_id=str(product.pk),
        active=product.status == ProductStatus.ACTIVE,
        name=product.name,
        exact_model=product.exact_model,
        target_price=target,
        payment_method=product.preferred_payment_method,
    )


def _legacy_link(source: OfferSource) -> ProductLink:
    return ProductLink(
        product_id=str(source.product_id),
        store=source.store,
        url=source.shared_offer.canonical_url,
        expected_seller=source.expected_seller,
        variant=source.expected_variant or source.product.variant,
    )


def _create_tenant_observation(
    run: MonitoringRun,
    source: OfferSource,
    collected: CollectedBase,
    profile: DeliveryProfile | None,
    *,
    shipping_quoter: ShippingQuoter,
    rate_limiter: StoreRateLimiter,
) -> PriceObservation:
    base = collected.observation
    status = _source_status(source, base)
    shipping_price_cents = None
    delivery_min_days = None
    delivery_max_days = None
    total_price_cents = None
    parser_version = base.parser_version or f"{source.store.casefold()}-shared-v1"

    if status == "READY_FOR_SHIPPING":
        if profile is None:
            status = "PROFILE_INCOMPLETE"
        else:
            try:
                rate_limiter.wait(source.store)
                quote = shipping_quoter(
                    _legacy_product(source.product),
                    _legacy_link(source),
                    profile.postal_code,
                    collected.html,
                    timeout=settings.COLLECTION_SHIPPING_TIMEOUT_SECONDS,
                    retries=0,
                )
                shipping_price_cents = _money_to_cents(quote.price)
                if shipping_price_cents is None:
                    raise ValueError("Frete sem preco")
                if quote.delivery_min_days is not None and quote.delivery_min_days < 0:
                    raise ValueError("Prazo minimo invalido")
                if quote.delivery_max_days is not None and quote.delivery_max_days < 0:
                    raise ValueError("Prazo maximo invalido")
                if (
                    quote.delivery_min_days is not None
                    and quote.delivery_max_days is not None
                    and quote.delivery_min_days > quote.delivery_max_days
                ):
                    raise ValueError("Intervalo de entrega invalido")
            except (ShippingNotAvailable, ValueError, KeyError, TypeError):
                status = "SHIPPING_UNAVAILABLE"
            else:
                delivery_min_days = quote.delivery_min_days
                delivery_max_days = quote.delivery_max_days
                total_price_cents = base.product_price_cents + shipping_price_cents
                parser_version = f"{parser_version}+shipping-v1"[:80]
                status = "OK"

    observation = PriceObservation.objects.create(
        tenant=source.tenant,
        run=run,
        product=source.product,
        source=source,
        shared_observation=base,
        observed_at=base.observed_at,
        seller=base.seller,
        payment_method=source.product.preferred_payment_method,
        product_price_cents=base.product_price_cents,
        shipping_price_cents=shipping_price_cents,
        total_price_cents=total_price_cents,
        delivery_min_days=delivery_min_days,
        delivery_max_days=delivery_max_days,
        in_stock=base.in_stock,
        status=status,
        parser_version=parser_version,
        error_message="" if status == "OK" else _safe_status_message(status),
    )
    if status not in {"OK", "OUT_OF_STOCK", "PROFILE_INCOMPLETE"}:
        CollectorError.objects.create(
            tenant=source.tenant,
            run=run,
            occurred_at=base.observed_at,
            context=f"source:{source.pk}",
            message=f"{status}: {_safe_status_message(status)}",
        )
    return observation


@transaction.atomic
def _establish_baseline(
    product_id,
    candidates: list[PriceObservation],
) -> bool:
    valid = [
        observation
        for observation in candidates
        if observation.status == "OK" and observation.total_price_cents is not None
    ]
    if not valid:
        return False
    product = MonitoredProduct.objects.select_for_update().get(pk=product_id)
    if product.baseline_total_cents is not None:
        return False
    baseline = min(valid, key=lambda item: (item.total_price_cents, item.pk))
    product.baseline_total_cents = baseline.total_price_cents
    product.baseline_observed_at = baseline.observed_at
    product.save(
        update_fields=["baseline_total_cents", "baseline_observed_at", "updated_at"]
    )
    baseline.is_baseline = True
    baseline.save(update_fields=["is_baseline"])
    return True


def collect_active_offers(
    *,
    fetcher: Fetcher = _default_fetcher,
    shipping_quoter: ShippingQuoter = quote_shipping,
    rate_limiter: StoreRateLimiter | None = None,
    max_sources: int | None = None,
) -> CollectionResult:
    started_at = timezone.now()
    source_queryset = (
        OfferSource.objects.filter(
            active=True,
            product__status=ProductStatus.ACTIVE,
        )
        .select_related("tenant", "product", "shared_offer")
        .order_by("url", "tenant_id", "id")
    )
    if max_sources is not None:
        if max_sources < 1:
            raise ValueError("max_sources deve ser positivo")
        source_queryset = source_queryset[:max_sources]
    sources = list(source_queryset)
    batch = CollectionBatch.objects.create(
        started_at=started_at,
        urls_planned=0,
    )
    try:
        return _execute_collection_batch(
            batch=batch,
            started_at=started_at,
            sources=sources,
            fetcher=fetcher,
            shipping_quoter=shipping_quoter,
            rate_limiter=rate_limiter,
        )
    except Exception as exc:
        finished_at = timezone.now()
        CollectionBatch.objects.filter(
            pk=batch.pk,
            status=CollectionBatchStatus.RUNNING,
        ).update(
            finished_at=finished_at,
            status=CollectionBatchStatus.FAILED,
            error_code="UNEXPECTED_ERROR",
        )
        MonitoringRun.objects.filter(
            collection_batch=batch,
            status=RunStatus.RUNNING,
        ).update(
            finished_at=finished_at,
            status=RunStatus.FAILED,
            error_message="Falha inesperada durante a coleta.",
        )
        retryable = not isinstance(exc, (ImproperlyConfigured, TypeError, ValueError))
        raise CollectionExecutionError(
            str(batch.pk),
            retryable=retryable,
        ) from exc


def _execute_collection_batch(
    *,
    batch: CollectionBatch,
    started_at,
    sources: list[OfferSource],
    fetcher: Fetcher,
    shipping_quoter: ShippingQuoter,
    rate_limiter: StoreRateLimiter | None,
) -> CollectionResult:

    sources_by_tenant: dict[object, list[OfferSource]] = defaultdict(list)
    for source in sources:
        sources_by_tenant[source.tenant_id].append(source)
    runs: dict[object, MonitoringRun] = {}
    for tenant_id, tenant_sources in sources_by_tenant.items():
        runs[tenant_id] = MonitoringRun.objects.create(
            tenant_id=tenant_id,
            collection_batch=batch,
            started_at=started_at,
            status=RunStatus.RUNNING,
            mode="SHARED_COLLECTION",
            input_file_name="web-panel",
            products_count=len({source.product_id for source in tenant_sources}),
            links_count=len(tenant_sources),
        )

    grouped: dict[object, tuple[SharedOffer, list[OfferSource]]] = {}
    invalid_sources: list[tuple[OfferSource, str]] = []
    for source in sources:
        try:
            exact = validate_exact_product_url(source.url)
        except ExactProductUrlError:
            invalid_sources.append((source, "INVALID_URL"))
            continue
        if exact.store != source.store:
            invalid_sources.append((source, "STORE_MISMATCH"))
            continue
        shared_offer, _created = SharedOffer.objects.get_or_create(
            canonical_url=exact.canonical_url,
            defaults={"store": exact.store},
        )
        if shared_offer.store != exact.store:
            invalid_sources.append((source, "STORE_MISMATCH"))
            continue
        if source.shared_offer_id != shared_offer.pk:
            OfferSource.objects.filter(pk=source.pk).update(shared_offer=shared_offer)
            source.shared_offer = shared_offer
        entry = grouped.get(shared_offer.pk)
        if entry is None:
            grouped[shared_offer.pk] = (shared_offer, [source])
        else:
            entry[1].append(source)

    batch.urls_planned = len(grouped) + len(invalid_sources)
    batch.save(update_fields=["urls_planned"])

    limiter = rate_limiter or StoreRateLimiter(settings.COLLECTION_REQUESTS_PER_MINUTE)
    collected_by_offer: dict[object, CollectedBase] = {}
    for shared_offer, _group_sources in sorted(
        grouped.values(),
        key=lambda item: (item[0].store, item[0].canonical_url),
    ):
        limiter.wait(shared_offer.store)
        collected_by_offer[shared_offer.pk] = _collect_base_offer(
            batch,
            shared_offer,
            fetcher=fetcher,
            clock=limiter.clock,
        )

    profiles = {
        profile.tenant_id: profile
        for profile in DeliveryProfile.objects.filter(tenant_id__in=runs)
    }
    observations_by_product: dict[object, list[PriceObservation]] = defaultdict(list)
    errors_found = False
    for source, status in invalid_sources:
        run = runs[source.tenant_id]
        observation = PriceObservation.objects.create(
            tenant=source.tenant,
            run=run,
            product=source.product,
            source=source,
            observed_at=started_at,
            seller="",
            payment_method=source.product.preferred_payment_method,
            in_stock=False,
            status=status,
            parser_version="url-router-v1",
            error_message=_safe_status_message(status),
        )
        CollectorError.objects.create(
            tenant=source.tenant,
            run=run,
            occurred_at=started_at,
            context=f"source:{source.pk}",
            message=f"{status}: {_safe_status_message(status)}",
        )
        observations_by_product[source.product_id].append(observation)
        errors_found = True

    for shared_offer, group_sources in grouped.values():
        collected = collected_by_offer[shared_offer.pk]
        for source in group_sources:
            observation = _create_tenant_observation(
                runs[source.tenant_id],
                source,
                collected,
                profiles.get(source.tenant_id),
                shipping_quoter=shipping_quoter,
                rate_limiter=limiter,
            )
            observations_by_product[source.product_id].append(observation)
            if observation.status not in {"OK", "OUT_OF_STOCK"}:
                errors_found = True

    baseline_product_ids = {
        product_id
        for product_id, candidates in observations_by_product.items()
        if _establish_baseline(product_id, candidates)
    }
    baselines_created = len(baseline_product_ids)
    alert_events_created = evaluate_collection_alerts(
        observations_by_product,
        baseline_product_ids=baseline_product_ids,
    )
    finished_at = timezone.now()
    for run in runs.values():
        run.finished_at = finished_at
        run.status = RunStatus.SUCCESS
        run.observations_count = PriceObservation.objects.filter(run=run).count()
        run.save(
            update_fields=["finished_at", "status", "observations_count"]
        )
    batch.finished_at = finished_at
    batch.status = (
        CollectionBatchStatus.PARTIAL if errors_found else CollectionBatchStatus.SUCCESS
    )
    batch.urls_fetched = len(collected_by_offer)
    batch.sources_processed = len(sources)
    batch.tenant_runs_count = len(runs)
    batch.error_code = "SOURCE_ERRORS" if errors_found else ""
    batch.save(
        update_fields=[
            "finished_at",
            "status",
            "urls_fetched",
            "sources_processed",
            "tenant_runs_count",
            "error_code",
        ]
    )
    LOGGER.info(
        "collection_batch_finished",
        extra={
            "event_code": "COLLECTION_BATCH_FINISHED",
            "status": batch.status,
            "batch_id": str(batch.pk),
            "count": len(sources),
        },
    )
    return CollectionResult(
        batch_id=str(batch.pk),
        status=batch.status,
        urls_planned=batch.urls_planned,
        urls_fetched=batch.urls_fetched,
        sources_processed=batch.sources_processed,
        tenant_runs_count=batch.tenant_runs_count,
        baselines_created=baselines_created,
        alert_events_created=alert_events_created,
    )
