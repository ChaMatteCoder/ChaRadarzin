from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

import requests
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from app.collectors.amazon import parse_amazon_html
from app.collectors.common import OfferParseError
from app.collectors.http import USER_AGENT
from app.collectors.kabum import parse_kabum_html
from monitoring.models import (
    MonitoredProduct,
    OfferSource,
    ProductLinkPreview,
    ProductPreviewStatus,
    ProductStatus,
    SupportedStore,
)
from monitoring.url_safety import (
    ExactProductUrlError,
    ensure_public_dns,
    validate_exact_product_url,
)


class ProductPreviewError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _price_to_cents(price: Decimal | None) -> int | None:
    if price is None:
        return None
    return int((price * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def fetch_preview_html(
    url: str,
    *,
    session: requests.Session | None = None,
    resolver=None,
    timeout_seconds: float | None = None,
    max_bytes: int | None = None,
) -> str:
    try:
        exact = validate_exact_product_url(url)
        if resolver is None:
            ensure_public_dns(exact.hostname)
        else:
            ensure_public_dns(exact.hostname, resolver=resolver)
    except ExactProductUrlError as exc:
        raise ProductPreviewError(exc.code, str(exc)) from exc
    owns_client = session is None
    client = session or requests.Session()
    request_timeout = timeout_seconds or settings.PRODUCT_PREVIEW_TIMEOUT_SECONDS
    response_limit = max_bytes or settings.PRODUCT_PREVIEW_MAX_BYTES
    try:
        response = client.get(
            exact.canonical_url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "pt-BR,pt;q=0.9",
                "Accept-Encoding": "identity",
            },
            timeout=request_timeout,
            allow_redirects=False,
            stream=True,
        )
    except requests.RequestException as exc:
        if owns_client:
            client.close()
        raise ProductPreviewError("FETCH_FAILED", "A loja nao respondeu a previa.") from exc
    try:
        if 300 <= response.status_code < 400:
            raise ProductPreviewError(
                "REDIRECT_REJECTED",
                "A loja redirecionou o link. Cole a URL final exibida no navegador.",
            )
        if response.status_code != 200:
            raise ProductPreviewError(
                "STORE_HTTP_ERROR",
                "A loja nao retornou uma pagina de produto valida.",
            )
        raw_length = response.headers.get("Content-Length", "")
        if raw_length.isdigit() and int(raw_length) > response_limit:
            raise ProductPreviewError("RESPONSE_TOO_LARGE", "A pagina excedeu o limite da previa.")
        content_type = response.headers.get("Content-Type", "")
        if content_type and "html" not in content_type.casefold():
            raise ProductPreviewError("NON_HTML_RESPONSE", "O link nao retornou uma pagina HTML.")
        chunks: list[bytes] = []
        received = 0
        for chunk in response.iter_content(chunk_size=65_536):
            if not chunk:
                continue
            received += len(chunk)
            if received > response_limit:
                raise ProductPreviewError("RESPONSE_TOO_LARGE", "A pagina excedeu o limite da previa.")
            chunks.append(chunk)
        encoding = response.encoding or "utf-8"
        return b"".join(chunks).decode(encoding, errors="replace")
    except requests.RequestException as exc:
        raise ProductPreviewError("FETCH_FAILED", "A loja nao respondeu a previa.") from exc
    finally:
        response.close()
        if owns_client:
            client.close()


def create_product_link_preview(
    user,
    submitted_url: str,
    *,
    target_product: MonitoredProduct | None = None,
    fetcher=None,
) -> ProductLinkPreview:
    if user.tenant_id is None:
        raise ProductPreviewError("TENANT_REQUIRED", "Conta sem espaco privado.")
    if target_product is not None and target_product.tenant_id != user.tenant_id:
        raise ProductPreviewError("CROSS_TENANT_PRODUCT", "Produto nao encontrado.")
    try:
        exact = validate_exact_product_url(submitted_url)
    except ExactProductUrlError as exc:
        raise ProductPreviewError(exc.code, str(exc)) from exc
    if OfferSource.objects.filter(
        tenant_id=user.tenant_id,
        url=exact.canonical_url,
        active=True,
    ).exists():
        raise ProductPreviewError("DUPLICATE_URL", "Este link ja faz parte do seu radar.")
    window_start = timezone.now() - timedelta(minutes=settings.PRODUCT_PREVIEW_WINDOW_MINUTES)
    attempts = ProductLinkPreview.objects.filter(
        tenant_id=user.tenant_id,
        created_at__gte=window_start,
    ).count()
    if attempts >= settings.PRODUCT_PREVIEW_RATE_LIMIT:
        raise ProductPreviewError(
            "PREVIEW_RATE_LIMIT",
            "Limite de previas atingido. Aguarde alguns minutos e tente novamente.",
        )
    preview = ProductLinkPreview.objects.create(
        tenant_id=user.tenant_id,
        requested_by=user,
        target_product=target_product,
        submitted_url=exact.submitted_url,
        canonical_url=exact.canonical_url,
        store=exact.store,
        expires_at=timezone.now() + timedelta(minutes=settings.PRODUCT_PREVIEW_TTL_MINUTES),
    )
    fetcher = fetcher or fetch_preview_html
    try:
        html = fetcher(exact.canonical_url)
        parsed = (
            parse_amazon_html(html)
            if exact.store == SupportedStore.AMAZON
            else parse_kabum_html(html)
        )
    except ProductPreviewError as exc:
        preview.status = ProductPreviewStatus.FAILED
        preview.error_code = exc.code[:80]
    except (OfferParseError, ValueError, KeyError, TypeError):
        preview.status = ProductPreviewStatus.FAILED
        preview.error_code = "PARSE_FAILED"
    else:
        preview.status = ProductPreviewStatus.READY
        preview.extracted_title = parsed.title[:500]
        preview.extracted_seller = parsed.seller[:255]
        preview.extracted_price_cents = _price_to_cents(parsed.product_price)
        preview.extracted_in_stock = parsed.in_stock
        preview.supports_pix = parsed.supports_pix
        preview.parser_version = parsed.parser_version[:80]
        preview.error_code = ""
    preview.save(
        update_fields=[
            "status",
            "extracted_title",
            "extracted_seller",
            "extracted_price_cents",
            "extracted_in_stock",
            "supports_pix",
            "parser_version",
            "error_code",
            "updated_at",
        ]
    )
    return preview


def expire_stale_product_previews() -> int:
    return ProductLinkPreview.objects.filter(
        status__in=(ProductPreviewStatus.PENDING, ProductPreviewStatus.READY),
        expires_at__lte=timezone.now(),
    ).update(status=ProductPreviewStatus.EXPIRED, updated_at=timezone.now())


def confirm_product_link_preview(
    preview: ProductLinkPreview,
    *,
    name: str,
    exact_model: str,
    variant: str,
    expected_seller: str,
    preferred_payment_method: str,
    target_price_cents: int | None,
) -> tuple[MonitoredProduct, OfferSource]:
    if preview.expires_at <= timezone.now():
        ProductLinkPreview.objects.filter(pk=preview.pk).exclude(
            status=ProductPreviewStatus.CONFIRMED
        ).update(status=ProductPreviewStatus.EXPIRED, updated_at=timezone.now())
        raise ProductPreviewError("PREVIEW_EXPIRED", "A previa expirou. Consulte o link novamente.")
    try:
        with transaction.atomic():
            locked = ProductLinkPreview.objects.select_for_update().get(pk=preview.pk)
            if locked.status == ProductPreviewStatus.CONFIRMED and locked.confirmed_source_id:
                return locked.confirmed_source.product, locked.confirmed_source
            if locked.expires_at <= timezone.now():
                raise ProductPreviewError("PREVIEW_EXPIRED", "A previa expirou. Consulte o link novamente.")
            if locked.status != ProductPreviewStatus.READY:
                raise ProductPreviewError("PREVIEW_NOT_READY", "A previa nao esta pronta para confirmacao.")

            product = locked.target_product
            if product is None:
                product = MonitoredProduct.objects.create(
                    tenant=locked.tenant,
                    name=name,
                    exact_model=exact_model,
                    variant=variant,
                    target_price_cents=target_price_cents,
                    preferred_payment_method=preferred_payment_method,
                    status=ProductStatus.ACTIVE,
                )
            else:
                if product.tenant_id != locked.tenant_id:
                    raise ProductPreviewError("CROSS_TENANT_PRODUCT", "Produto nao encontrado.")
                product.name = name
                product.exact_model = exact_model
                product.variant = variant
                product.target_price_cents = target_price_cents
                product.preferred_payment_method = preferred_payment_method
                if product.status == ProductStatus.ARCHIVED:
                    product.status = ProductStatus.ACTIVE
                product.save(
                    update_fields=[
                        "name",
                        "exact_model",
                        "variant",
                        "target_price_cents",
                        "preferred_payment_method",
                        "status",
                        "updated_at",
                    ]
                )
            source = (
                OfferSource.objects.select_for_update()
                .filter(
                    tenant=locked.tenant,
                    product=product,
                    url=locked.canonical_url,
                    active=False,
                )
                .first()
            )
            if source is None:
                source = OfferSource.objects.create(
                    tenant=locked.tenant,
                    product=product,
                    store=locked.store,
                    url=locked.canonical_url,
                    expected_seller=expected_seller,
                    expected_variant=variant,
                    active=True,
                )
            else:
                source.store = locked.store
                source.expected_seller = expected_seller
                source.expected_variant = variant
                source.active = True
                source.save(
                    update_fields=[
                        "store",
                        "expected_seller",
                        "expected_variant",
                        "active",
                        "updated_at",
                    ]
                )
            locked.status = ProductPreviewStatus.CONFIRMED
            locked.confirmed_source = source
            locked.confirmed_at = timezone.now()
            locked.save(
                update_fields=["status", "confirmed_source", "confirmed_at", "updated_at"]
            )
            return product, source
    except IntegrityError as exc:
        raise ProductPreviewError("DUPLICATE_URL", "Este link ja faz parte do seu radar.") from exc
