from __future__ import annotations

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from tenancy.models import PaymentMethod, Tenant


class TenantOwnedQuerySet(models.QuerySet):
    def for_tenant(self, tenant: Tenant | None):
        if tenant is None:
            return self.none()
        return self.filter(tenant=tenant)

    def for_user(self, user):
        if not user.is_authenticated or user.tenant_id is None:
            return self.none()
        return self.filter(tenant_id=user.tenant_id)


class TenantOwnedModel(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)

    objects = TenantOwnedQuerySet.as_manager()

    class Meta:
        abstract = True


class ProductStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Ativo"
    PAUSED = "PAUSED", "Pausado"
    ARCHIVED = "ARCHIVED", "Arquivado"


class SupportedStore(models.TextChoices):
    AMAZON = "Amazon", "Amazon Brasil"
    KABUM = "KaBuM", "KaBuM"


class MonitoredProduct(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    legacy_product_id = models.CharField(max_length=100, blank=True)
    name = models.CharField(max_length=255)
    exact_model = models.CharField(max_length=255, blank=True)
    variant = models.CharField(max_length=255, blank=True)
    target_price_cents = models.PositiveBigIntegerField(null=True, blank=True)
    baseline_total_cents = models.PositiveBigIntegerField(null=True, blank=True)
    baseline_observed_at = models.DateTimeField(null=True, blank=True)
    preferred_payment_method = models.CharField(
        max_length=16,
        choices=PaymentMethod.choices,
        default=PaymentMethod.PIX,
    )
    status = models.CharField(
        max_length=16,
        choices=ProductStatus.choices,
        default=ProductStatus.ACTIVE,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("tenant", "legacy_product_id"),
                condition=~models.Q(legacy_product_id=""),
                name="product_tenant_legacy_id_unique",
            )
        ]

    def __str__(self) -> str:
        return self.name


class OfferSource(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(
        MonitoredProduct,
        on_delete=models.CASCADE,
        related_name="sources",
    )
    shared_offer = models.ForeignKey(
        "SharedOffer",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="tenant_sources",
    )
    store = models.CharField(max_length=80, choices=SupportedStore.choices)
    url = models.URLField(max_length=2048)
    expected_seller = models.CharField(max_length=255, blank=True)
    expected_variant = models.CharField(max_length=255, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("store", "url")
        constraints = [
            models.UniqueConstraint(
                fields=("tenant", "product", "url"),
                name="source_tenant_product_url_unique",
            ),
            models.UniqueConstraint(
                fields=("tenant", "url"),
                condition=models.Q(active=True),
                name="source_tenant_url_unique",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        if self.product_id and self.tenant_id != self.product.tenant_id:
            raise ValidationError("A fonte e o produto devem pertencer ao mesmo tenant.")


class ProductPreviewStatus(models.TextChoices):
    PENDING = "PENDING", "Consultando"
    READY = "READY", "Aguardando confirmacao"
    FAILED = "FAILED", "Falha na previa"
    CONFIRMED = "CONFIRMED", "Confirmado"
    EXPIRED = "EXPIRED", "Expirado"


class ProductLinkPreview(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="product_link_previews",
    )
    target_product = models.ForeignKey(
        MonitoredProduct,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="link_previews",
    )
    confirmed_source = models.OneToOneField(
        OfferSource,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="confirmation_preview",
    )
    submitted_url = models.URLField(max_length=2048)
    canonical_url = models.URLField(max_length=2048)
    store = models.CharField(max_length=80, choices=SupportedStore.choices)
    status = models.CharField(
        max_length=16,
        choices=ProductPreviewStatus.choices,
        default=ProductPreviewStatus.PENDING,
    )
    extracted_title = models.CharField(max_length=500, blank=True)
    extracted_seller = models.CharField(max_length=255, blank=True)
    extracted_price_cents = models.PositiveBigIntegerField(null=True, blank=True)
    extracted_in_stock = models.BooleanField(null=True, blank=True)
    supports_pix = models.BooleanField(default=False)
    parser_version = models.CharField(max_length=80, blank=True)
    error_code = models.CharField(max_length=80, blank=True)
    expires_at = models.DateTimeField()
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("tenant", "status", "created_at")),
            models.Index(fields=("expires_at",)),
        ]

    def clean(self) -> None:
        super().clean()
        if self.requested_by_id and self.requested_by.tenant_id != self.tenant_id:
            raise ValidationError("A previa e o usuario devem pertencer ao mesmo tenant.")
        if self.target_product_id and self.target_product.tenant_id != self.tenant_id:
            raise ValidationError("A previa e o produto devem pertencer ao mesmo tenant.")
        if self.confirmed_source_id and self.confirmed_source.tenant_id != self.tenant_id:
            raise ValidationError("A previa e a fonte devem pertencer ao mesmo tenant.")
        if (
            self.confirmed_source_id
            and self.target_product_id
            and self.confirmed_source.product_id != self.target_product_id
        ):
            raise ValidationError("A fonte confirmada deve pertencer ao produto da previa.")


class CollectionBatchStatus(models.TextChoices):
    RUNNING = "RUNNING", "Em execucao"
    SUCCESS = "SUCCESS", "Sucesso"
    PARTIAL = "PARTIAL", "Parcial"
    FAILED = "FAILED", "Falha"


class CollectionBatch(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=16,
        choices=CollectionBatchStatus.choices,
        default=CollectionBatchStatus.RUNNING,
    )
    urls_planned = models.PositiveIntegerField(default=0)
    urls_fetched = models.PositiveIntegerField(default=0)
    sources_processed = models.PositiveIntegerField(default=0)
    tenant_runs_count = models.PositiveIntegerField(default=0)
    error_code = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ("-started_at", "-id")


class CollectionJobType(models.TextChoices):
    DAILY_COLLECTION = "DAILY_COLLECTION", "Coleta diaria"
    MANUAL_COLLECTION = "MANUAL_COLLECTION", "Coleta manual"


class CollectionJobStatus(models.TextChoices):
    QUEUED = "QUEUED", "Na fila"
    RUNNING = "RUNNING", "Em execucao"
    RETRY_WAIT = "RETRY_WAIT", "Aguardando nova tentativa"
    SUCCESS = "SUCCESS", "Sucesso"
    DEAD = "DEAD", "Esgotado"


class CollectionJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job_type = models.CharField(
        max_length=32,
        choices=CollectionJobType.choices,
        default=CollectionJobType.DAILY_COLLECTION,
    )
    idempotency_key = models.CharField(max_length=160, unique=True)
    status = models.CharField(
        max_length=16,
        choices=CollectionJobStatus.choices,
        default=CollectionJobStatus.QUEUED,
    )
    scheduled_for = models.DateTimeField()
    available_at = models.DateTimeField()
    attempt_count = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=4)
    claimed_by = models.CharField(max_length=128, blank=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    duration_ms = models.PositiveBigIntegerField(default=0)
    collection_batch = models.ForeignKey(
        CollectionBatch,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="jobs",
    )
    last_error_code = models.CharField(max_length=80, blank=True)
    last_error_message = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("scheduled_for", "created_at", "id")
        indexes = [
            models.Index(fields=("status", "available_at")),
            models.Index(fields=("job_type", "scheduled_for")),
        ]


class CollectionJobAttemptStatus(models.TextChoices):
    RUNNING = "RUNNING", "Em execucao"
    SUCCESS = "SUCCESS", "Sucesso"
    FAILED = "FAILED", "Falha"
    ABANDONED = "ABANDONED", "Worker interrompido"


class CollectionJobAttempt(models.Model):
    job = models.ForeignKey(
        CollectionJob,
        on_delete=models.CASCADE,
        related_name="attempts",
    )
    number = models.PositiveSmallIntegerField()
    worker_id = models.CharField(max_length=128)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=16,
        choices=CollectionJobAttemptStatus.choices,
        default=CollectionJobAttemptStatus.RUNNING,
    )
    duration_ms = models.PositiveBigIntegerField(default=0)
    collection_batch = models.ForeignKey(
        CollectionBatch,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="job_attempts",
    )
    error_code = models.CharField(max_length=80, blank=True)
    error_message = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ("job", "number")
        constraints = [
            models.UniqueConstraint(
                fields=("job", "number"),
                name="collection_job_attempt_number_unique",
            )
        ]


class CollectionWorkerLease(models.Model):
    key = models.CharField(max_length=64, primary_key=True)
    owner = models.CharField(max_length=128, blank=True)
    acquired_at = models.DateTimeField(null=True, blank=True)
    locked_until = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class SharedOffer(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.CharField(max_length=80, choices=SupportedStore.choices)
    canonical_url = models.URLField(max_length=2048, unique=True)
    last_collected_at = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("store", "canonical_url")

    def __str__(self) -> str:
        return f"{self.store}: {self.canonical_url}"


class SharedOfferObservation(models.Model):
    batch = models.ForeignKey(
        CollectionBatch,
        on_delete=models.CASCADE,
        related_name="base_observations",
    )
    shared_offer = models.ForeignKey(
        SharedOffer,
        on_delete=models.PROTECT,
        related_name="observations",
    )
    observed_at = models.DateTimeField()
    title = models.CharField(max_length=500, blank=True)
    seller = models.CharField(max_length=255, blank=True)
    product_price_cents = models.PositiveBigIntegerField(null=True, blank=True)
    in_stock = models.BooleanField(default=False)
    supports_pix = models.BooleanField(default=False)
    price_payment_method = models.CharField(max_length=16, blank=True)
    status = models.CharField(max_length=40)
    parser_version = models.CharField(max_length=80, blank=True)
    error_code = models.CharField(max_length=80, blank=True)
    duration_ms = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("observed_at", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("batch", "shared_offer"),
                name="base_observation_batch_offer_unique",
            )
        ]
        indexes = [models.Index(fields=("shared_offer", "observed_at"))]


class RunStatus(models.TextChoices):
    RUNNING = "RUNNING", "Em execucao"
    SUCCESS = "SUCCESS", "Sucesso"
    FAILED = "FAILED", "Falha"


class MonitoringRun(TenantOwnedModel):
    legacy_id = models.PositiveBigIntegerField(null=True, blank=True)
    collection_batch = models.ForeignKey(
        CollectionBatch,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tenant_runs",
    )
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=RunStatus.choices)
    mode = models.CharField(max_length=40)
    input_file_name = models.CharField(max_length=255)
    products_count = models.PositiveIntegerField(default=0)
    links_count = models.PositiveIntegerField(default=0)
    observations_count = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ("-started_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("tenant", "legacy_id"),
                condition=models.Q(legacy_id__isnull=False),
                name="run_tenant_legacy_id_unique",
            )
        ]


class PriceObservation(TenantOwnedModel):
    legacy_id = models.PositiveBigIntegerField(null=True, blank=True)
    run = models.ForeignKey(
        MonitoringRun,
        on_delete=models.CASCADE,
        related_name="observations",
    )
    product = models.ForeignKey(
        MonitoredProduct,
        on_delete=models.CASCADE,
        related_name="observations",
    )
    source = models.ForeignKey(
        OfferSource,
        on_delete=models.PROTECT,
        related_name="observations",
    )
    shared_observation = models.ForeignKey(
        SharedOfferObservation,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="tenant_observations",
    )
    observed_at = models.DateTimeField()
    seller = models.CharField(max_length=255)
    payment_method = models.CharField(max_length=40)
    product_price_cents = models.BigIntegerField(null=True, blank=True)
    shipping_price_cents = models.BigIntegerField(null=True, blank=True)
    total_price_cents = models.BigIntegerField(null=True, blank=True)
    delivery_min_days = models.IntegerField(null=True, blank=True)
    delivery_max_days = models.IntegerField(null=True, blank=True)
    in_stock = models.BooleanField()
    status = models.CharField(max_length=40)
    parser_version = models.CharField(max_length=80)
    error_message = models.TextField(blank=True)
    is_baseline = models.BooleanField(default=False)

    class Meta:
        ordering = ("observed_at", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("tenant", "legacy_id"),
                condition=models.Q(legacy_id__isnull=False),
                name="observation_tenant_legacy_id_unique",
            )
        ]
        indexes = [models.Index(fields=("tenant", "product", "observed_at"))]

    def clean(self) -> None:
        super().clean()
        related_tenants = {
            self.run.tenant_id if self.run_id else None,
            self.product.tenant_id if self.product_id else None,
            self.source.tenant_id if self.source_id else None,
        }
        related_tenants.discard(None)
        if related_tenants and related_tenants != {self.tenant_id}:
            raise ValidationError("Observacao e relacionamentos devem usar o mesmo tenant.")
        if (
            self.shared_observation_id
            and self.source_id
            and self.source.shared_offer_id
            and self.shared_observation.shared_offer_id != self.source.shared_offer_id
        ):
            raise ValidationError("A observacao compartilhada nao corresponde a fonte.")


class AlertRule(TenantOwnedModel):
    product = models.ForeignKey(
        MonitoredProduct,
        on_delete=models.CASCADE,
        related_name="alert_rules",
    )
    minimum_price_drop_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default="1.00",
    )
    alert_new_historical_low = models.BooleanField(default=True)
    alert_target_reached = models.BooleanField(default=True)
    alert_back_in_stock = models.BooleanField(default=True)
    alert_price_increase = models.BooleanField(default=False)
    cooldown_minutes = models.PositiveIntegerField(default=360)
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("tenant_id", "product_id")
        constraints = [
            models.UniqueConstraint(
                fields=("tenant", "product"),
                name="alert_rule_tenant_product_unique",
            )
        ]
        indexes = [models.Index(fields=("tenant", "enabled"))]

    def clean(self) -> None:
        super().clean()
        if self.product_id and self.product.tenant_id != self.tenant_id:
            raise ValidationError("A regra e o produto devem pertencer ao mesmo tenant.")
        if self.minimum_price_drop_percent < 0 or self.minimum_price_drop_percent > 100:
            raise ValidationError("A queda minima deve estar entre 0% e 100%.")


class AlertEventStatus(models.TextChoices):
    CREATED = "CREATED", "Criado"


class NotificationDeliveryStatus(models.TextChoices):
    PENDING = "PENDING", "Pendente"
    SENDING = "SENDING", "Enviando"
    SENT = "SENT", "Enviado"
    FAILED = "FAILED", "Falhou"
    BLOCKED = "BLOCKED", "Bloqueado"


class AlertEvent(TenantOwnedModel):
    product = models.ForeignKey(
        MonitoredProduct,
        on_delete=models.CASCADE,
        related_name="alert_events",
    )
    observation = models.ForeignKey(
        PriceObservation,
        on_delete=models.CASCADE,
        related_name="alert_events",
    )
    event_type = models.CharField(max_length=255)
    status = models.CharField(
        max_length=16,
        choices=AlertEventStatus.choices,
        default=AlertEventStatus.CREATED,
    )
    idempotency_key = models.CharField(max_length=255, unique=True)
    occurred_at = models.DateTimeField()
    current_price_cents = models.PositiveBigIntegerField()
    previous_price_cents = models.PositiveBigIntegerField(null=True, blank=True)
    historical_low_cents = models.PositiveBigIntegerField(null=True, blank=True)
    target_price_cents = models.PositiveBigIntegerField(null=True, blank=True)
    variation_percent = models.DecimalField(
        max_digits=8,
        decimal_places=3,
        null=True,
        blank=True,
    )
    reason = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-occurred_at", "-id")
        indexes = [
            models.Index(fields=("tenant", "product", "occurred_at")),
            models.Index(fields=("tenant", "status", "created_at")),
        ]

    def clean(self) -> None:
        super().clean()
        related_tenants = {
            self.product.tenant_id if self.product_id else None,
            self.observation.tenant_id if self.observation_id else None,
        }
        related_tenants.discard(None)
        if related_tenants and related_tenants != {self.tenant_id}:
            raise ValidationError("O evento e seus relacionamentos devem usar o mesmo tenant.")
        if self.observation_id and self.product_id:
            if self.observation.product_id != self.product_id:
                raise ValidationError("O evento deve apontar para a observacao do produto.")


class CollectorError(TenantOwnedModel):
    legacy_id = models.PositiveBigIntegerField(null=True, blank=True)
    run = models.ForeignKey(
        MonitoringRun,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="collector_errors",
    )
    occurred_at = models.DateTimeField()
    context = models.CharField(max_length=255)
    message = models.TextField()

    class Meta:
        ordering = ("occurred_at", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("tenant", "legacy_id"),
                condition=models.Q(legacy_id__isnull=False),
                name="collector_error_tenant_legacy_id_unique",
            )
        ]


class NotificationDelivery(TenantOwnedModel):
    legacy_id = models.PositiveBigIntegerField(null=True, blank=True)
    run = models.ForeignKey(
        MonitoringRun,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="notification_deliveries",
    )
    product = models.ForeignKey(
        MonitoredProduct,
        on_delete=models.CASCADE,
        related_name="notification_deliveries",
    )
    alert_event = models.ForeignKey(
        "AlertEvent",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="deliveries",
    )
    delivery_key = models.CharField(max_length=255, unique=True, null=True, blank=True)
    attempted_at = models.DateTimeField()
    channel = models.CharField(max_length=40)
    event_type = models.CharField(max_length=255)
    price_cents = models.BigIntegerField()
    previous_price_cents = models.BigIntegerField(null=True, blank=True)
    status = models.CharField(max_length=16)
    error_message = models.TextField(blank=True)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    message_id = models.BigIntegerField(null=True, blank=True)
    error_code = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ("attempted_at", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("tenant", "legacy_id"),
                condition=models.Q(legacy_id__isnull=False),
                name="delivery_tenant_legacy_id_unique",
            ),
            models.UniqueConstraint(
                fields=("alert_event", "channel"),
                condition=models.Q(alert_event__isnull=False),
                name="delivery_alert_event_channel_unique",
            ),
        ]
        indexes = [models.Index(fields=("tenant", "status", "attempted_at"))]


class LegacyImportBatch(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_file_name = models.CharField(max_length=255)
    source_sha256 = models.CharField(max_length=64)
    source_counts = models.JSONField(default=dict)
    imported_counts = models.JSONField(default=dict)
    alert_cursor_at = models.DateTimeField(null=True, blank=True)
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-imported_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("tenant", "source_sha256"),
                name="legacy_batch_tenant_hash_unique",
            )
        ]
