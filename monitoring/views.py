from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, OuterRef, Prefetch, Q, Subquery
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from monitoring.forms import (
    DeliveryProfileForm,
    ExactProductLinkForm,
    ProductConfirmationForm,
    ProductEditForm,
    SourceEditForm,
    cents_as_input,
)
from monitoring.history import build_product_history
from monitoring.models import (
    AlertRule,
    AlertEvent,
    MonitoredProduct,
    NotificationDelivery,
    NotificationDeliveryStatus,
    OfferSource,
    PriceObservation,
    ProductLinkPreview,
    ProductStatus,
)
from monitoring.preview import (
    ProductPreviewError,
    confirm_product_link_preview,
    create_product_link_preview,
    expire_stale_product_previews,
)
from monitoring.services import archive_product, collection_state_for, toggle_product_pause
from telegram_bots.models import ManagedBot
from tenancy.models import DeliveryProfile


def landing(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("dashboard")
    return render(
        request,
        "landing.html",
        {"telegram_login_enabled": settings.TELEGRAM_OIDC_ENABLED},
    )


def _ensure_tenant(request: HttpRequest) -> None:
    if request.user.tenant_id is None:
        raise PermissionDenied("Conta sem tenant.")


def _product_for_request(request: HttpRequest, product_id) -> MonitoredProduct:
    _ensure_tenant(request)
    return get_object_or_404(
        MonitoredProduct,
        pk=product_id,
        tenant_id=request.user.tenant_id,
    )


def _decorate_collection_state(products) -> list[MonitoredProduct]:
    decorated = list(products)
    for product in decorated:
        product.collection_state = collection_state_for(
            product,
            getattr(product, "latest_collection_status", None),
            getattr(product, "source_count", None),
        )
    return decorated


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    _ensure_tenant(request)
    latest = PriceObservation.objects.filter(product=OuterRef("pk")).order_by(
        "-observed_at",
        "-id",
    )
    products = _decorate_collection_state(
        MonitoredProduct.objects.for_user(request.user)
        .exclude(status=ProductStatus.ARCHIVED)
        .annotate(
            source_count=Count("sources", filter=Q(sources__active=True), distinct=True),
            latest_collection_status=Subquery(latest.values("status")[:1]),
            latest_collected_at=Subquery(latest.values("observed_at")[:1]),
        )
        .prefetch_related(
            Prefetch(
                "sources",
                queryset=OfferSource.objects.filter(active=True),
                to_attr="active_sources",
            )
        )
        .order_by("name", "id")
    )
    for product in products:
        product.history = build_product_history(
            product,
            sources=getattr(product, "active_sources", ()),
        )
    personal_bot = ManagedBot.objects.filter(tenant_id=request.user.tenant_id).first()
    delivery_profile = DeliveryProfile.objects.filter(tenant_id=request.user.tenant_id).first()
    return render(
        request,
        "dashboard.html",
        {
            "products": products,
            "personal_bot": personal_bot,
            "delivery_profile": delivery_profile,
        },
    )


@login_required
def product_link_preview_create(
    request: HttpRequest,
    product_id=None,
) -> HttpResponse:
    _ensure_tenant(request)
    product = _product_for_request(request, product_id) if product_id else None
    if product is not None and product.status == ProductStatus.ARCHIVED:
        raise Http404
    form = ExactProductLinkForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            preview = create_product_link_preview(
                request.user,
                form.cleaned_data["url"],
                target_product=product,
            )
        except ProductPreviewError as exc:
            form.add_error("url", str(exc))
        else:
            return redirect("product-preview-confirm", preview_id=preview.pk)
    return render(
        request,
        "monitoring/product_link_form.html",
        {"form": form, "product": product},
    )


def _confirmation_initial(preview: ProductLinkPreview) -> dict:
    product = preview.target_product
    profile = DeliveryProfile.objects.filter(tenant=preview.tenant).first()
    return {
        "name": product.name if product else preview.extracted_title[:255],
        "exact_model": product.exact_model if product else "",
        "variant": product.variant if product else "",
        "expected_seller": preview.extracted_seller,
        "preferred_payment_method": (
            product.preferred_payment_method
            if product
            else profile.preferred_payment_method if profile else "PIX"
        ),
        "target_price": cents_as_input(product.target_price_cents) if product else "",
    }


@login_required
def product_preview_confirm(request: HttpRequest, preview_id) -> HttpResponse:
    _ensure_tenant(request)
    expire_stale_product_previews()
    preview = get_object_or_404(
        ProductLinkPreview.objects.select_related("target_product"),
        pk=preview_id,
        tenant_id=request.user.tenant_id,
        requested_by=request.user,
    )
    form = ProductConfirmationForm(
        request.POST or None,
        initial=_confirmation_initial(preview),
    )
    if request.method == "POST" and form.is_valid():
        try:
            product, _source = confirm_product_link_preview(
                preview,
                name=form.cleaned_data["name"],
                exact_model=form.cleaned_data["exact_model"],
                variant=form.cleaned_data["variant"],
                expected_seller=form.cleaned_data["expected_seller"],
                preferred_payment_method=form.cleaned_data["preferred_payment_method"],
                target_price_cents=form.cleaned_data["target_price"],
            )
        except ProductPreviewError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Produto ativado. A primeira coleta criará a baseline.")
            return redirect("product-detail-page", product_id=product.pk)
    return render(
        request,
        "monitoring/product_confirm.html",
        {"preview": preview, "form": form},
    )


@login_required
def product_detail(request: HttpRequest, product_id) -> HttpResponse:
    product = _product_for_request(request, product_id)
    latest_observation = (
        PriceObservation.objects.filter(product=product, tenant=product.tenant)
        .select_related("source")
        .order_by("-observed_at", "-id")
        .first()
    )
    sources = list(
        OfferSource.objects.filter(product=product, tenant=product.tenant).order_by(
            "store",
            "created_at",
        )
    )
    history = build_product_history(
        product,
        sources=[source for source in sources if source.active],
    )
    events = list(
        AlertEvent.objects.filter(
            product=product,
            tenant_id=request.user.tenant_id,
        )
        .prefetch_related(
            Prefetch(
                "deliveries",
                queryset=NotificationDelivery.objects.filter(
                    channel="TELEGRAM_PERSONAL",
                ).order_by("-attempted_at", "-id"),
                to_attr="personal_deliveries",
            )
        )
        .order_by("-occurred_at", "-id")[:12]
    )
    delivery_labels = {
        NotificationDeliveryStatus.PENDING: "Pendente",
        NotificationDeliveryStatus.SENDING: "Enviando",
        NotificationDeliveryStatus.SENT: "Enviado",
        NotificationDeliveryStatus.FAILED: "Falhou",
        NotificationDeliveryStatus.BLOCKED: "Bloqueado",
    }
    for event in events:
        delivery = next(iter(getattr(event, "personal_deliveries", ())), None)
        event.delivery = delivery
        event.delivery_status_label = (
            delivery_labels.get(delivery.status, delivery.status)
            if delivery
            else "Ainda não processado"
        )
    product.collection_state = collection_state_for(
        product,
        latest_observation.status if latest_observation else None,
        sum(1 for source in sources if source.active),
    )
    return render(
        request,
        "monitoring/product_detail.html",
        {
            "product": product,
            "sources": sources,
            "latest_observation": latest_observation,
            "history": history,
            "alert_events": events,
            "personal_bot": ManagedBot.objects.filter(
                tenant_id=request.user.tenant_id
            ).first(),
        },
    )


@login_required
def product_edit(request: HttpRequest, product_id) -> HttpResponse:
    product = _product_for_request(request, product_id)
    if product.status == ProductStatus.ARCHIVED:
        raise Http404
    form = ProductEditForm(request.POST or None, instance=product)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Produto atualizado.")
        return redirect("product-detail-page", product_id=product.pk)
    return render(request, "monitoring/product_edit.html", {"product": product, "form": form})


@login_required
@require_POST
def product_toggle_pause(request: HttpRequest, product_id) -> HttpResponse:
    product = _product_for_request(request, product_id)
    if product.status == ProductStatus.ARCHIVED:
        raise Http404
    product = toggle_product_pause(product)
    messages.success(
        request,
        "Monitoramento retomado." if product.status == ProductStatus.ACTIVE else "Monitoramento pausado.",
    )
    return redirect("product-detail-page", product_id=product.pk)


@login_required
@require_POST
def product_archive(request: HttpRequest, product_id) -> HttpResponse:
    product = _product_for_request(request, product_id)
    archive_product(product)
    messages.success(request, "Produto removido do radar; o histórico foi preservado.")
    return redirect("dashboard")


@login_required
def source_edit(request: HttpRequest, product_id, source_id) -> HttpResponse:
    product = _product_for_request(request, product_id)
    if product.status == ProductStatus.ARCHIVED:
        raise Http404
    source = get_object_or_404(
        OfferSource,
        pk=source_id,
        product=product,
        tenant_id=request.user.tenant_id,
    )
    form = SourceEditForm(request.POST or None, instance=source)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Validação da fonte atualizada.")
        return redirect("product-detail-page", product_id=product.pk)
    return render(
        request,
        "monitoring/source_edit.html",
        {"product": product, "source": source, "form": form},
    )


@login_required
@require_POST
def source_toggle(request: HttpRequest, product_id, source_id) -> HttpResponse:
    product = _product_for_request(request, product_id)
    if product.status == ProductStatus.ARCHIVED:
        raise Http404
    source = get_object_or_404(
        OfferSource,
        pk=source_id,
        product=product,
        tenant_id=request.user.tenant_id,
    )
    source.active = not source.active
    source.save(update_fields=["active", "updated_at"])
    messages.success(
        request,
        (
            "Fonte ativada."
            if source.active
            else "Fonte pausada. O historico foi preservado e o link pode ser associado a outro produto."
        ),
    )
    return redirect("product-detail-page", product_id=product.pk)


@login_required
def delivery_settings(request: HttpRequest) -> HttpResponse:
    _ensure_tenant(request)
    profile = DeliveryProfile.objects.filter(tenant_id=request.user.tenant_id).first()
    form = DeliveryProfileForm(request.POST or None, instance=profile)
    if request.method == "POST" and form.is_valid():
        profile = form.save(commit=False)
        profile.tenant_id = request.user.tenant_id
        profile.save()
        AlertRule.objects.filter(tenant_id=request.user.tenant_id).update(
            minimum_price_drop_percent=profile.minimum_price_drop_percent,
            alert_price_increase=profile.alert_price_increase,
            updated_at=profile.updated_at,
        )
        messages.success(request, "Preferências do radar atualizadas.")
        return redirect("delivery-settings")
    return render(
        request,
        "monitoring/settings.html",
        {"form": form, "profile": profile},
    )
