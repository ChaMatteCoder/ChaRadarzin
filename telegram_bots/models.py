from __future__ import annotations

import uuid

from django.db import models

from tenancy.models import Tenant


class ManagedBotStatus(models.TextChoices):
    CONFIGURING = "CONFIGURING", "Configurando"
    AWAITING_START = "AWAITING_START", "Aguardando /start"
    ACTIVE = "ACTIVE", "Conectado"
    BLOCKED = "BLOCKED", "Bloqueado pelo proprietario"
    REVOKED = "REVOKED", "Token revogado"
    OWNER_CHANGED = "OWNER_CHANGED", "Proprietario alterado"
    WEBHOOK_ERROR = "WEBHOOK_ERROR", "Webhook com erro"
    DISCONNECTED = "DISCONNECTED", "Desconectado"
    ERROR = "ERROR", "Erro de configuracao"


class BotCredentialSource(models.TextChoices):
    MANAGED = "MANAGED", "Managed bot"
    MANUAL_FALLBACK = "MANUAL_FALLBACK", "Fallback interno"


class ManagedBot(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.OneToOneField(
        Tenant,
        on_delete=models.CASCADE,
        related_name="managed_bot",
    )
    telegram_bot_id = models.BigIntegerField(unique=True)
    owner_telegram_user_id = models.BigIntegerField()
    username = models.CharField(max_length=64, unique=True)
    display_name = models.CharField(max_length=160)
    credential_source = models.CharField(
        max_length=24,
        choices=BotCredentialSource.choices,
        default=BotCredentialSource.MANAGED,
    )
    status = models.CharField(
        max_length=24,
        choices=ManagedBotStatus.choices,
        default=ManagedBotStatus.CONFIGURING,
    )
    token_ciphertext = models.TextField(blank=True, editable=False)
    token_key_version = models.PositiveSmallIntegerField(null=True, editable=False)
    webhook_public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    webhook_secret_digest = models.CharField(max_length=64, blank=True, editable=False)
    chat_id = models.BigIntegerField(null=True, blank=True)
    access_restricted = models.BooleanField(default=False)
    activated_at = models.DateTimeField(null=True, blank=True)
    token_rotated_at = models.DateTimeField(null=True, blank=True)
    disconnected_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    last_health_check_at = models.DateTimeField(null=True, blank=True)
    last_error_code = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("created_at",)

    @property
    def has_token(self) -> bool:
        return bool(self.token_ciphertext and self.token_key_version is not None)

    def __str__(self) -> str:
        return f"@{self.username} ({self.get_status_display()})"


class OnboardingStatus(models.TextChoices):
    PENDING = "PENDING", "Aguardando criacao"
    CONFIGURING = "CONFIGURING", "Configurando"
    AWAITING_START = "AWAITING_START", "Aguardando /start"
    ACTIVE = "ACTIVE", "Concluido"
    EXPIRED = "EXPIRED", "Expirado"
    ABANDONED = "ABANDONED", "Abandonado"
    FAILED = "FAILED", "Falhou"


class BotOnboardingSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="bot_onboarding_sessions",
    )
    telegram_user_id = models.BigIntegerField()
    bot = models.ForeignKey(
        ManagedBot,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="onboarding_sessions",
    )
    suggested_name = models.CharField(max_length=64)
    suggested_username = models.CharField(max_length=32)
    status = models.CharField(
        max_length=24,
        choices=OnboardingStatus.choices,
        default=OnboardingStatus.PENDING,
    )
    is_open = models.BooleanField(default=True)
    expires_at = models.DateTimeField()
    error_code = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("tenant",),
                condition=models.Q(is_open=True),
                name="one_open_bot_onboarding_per_tenant",
            )
        ]
        indexes = [models.Index(fields=("is_open", "expires_at"))]


class UpdateProcessingStatus(models.TextChoices):
    PROCESSING = "PROCESSING", "Processando"
    PROCESSED = "PROCESSED", "Processado"
    FAILED = "FAILED", "Falhou"
    REJECTED = "REJECTED", "Rejeitado"


class TelegramUpdateReceipt(models.Model):
    endpoint_key = models.CharField(max_length=64)
    update_id = models.BigIntegerField()
    bot = models.ForeignKey(
        ManagedBot,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="update_receipts",
    )
    update_kind = models.CharField(max_length=40)
    status = models.CharField(
        max_length=16,
        choices=UpdateProcessingStatus.choices,
        default=UpdateProcessingStatus.PROCESSING,
    )
    outcome_code = models.CharField(max_length=80, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("endpoint_key", "update_id"),
                name="telegram_endpoint_update_unique",
            )
        ]
        ordering = ("-received_at",)


class ManualRefreshStatus(models.TextChoices):
    PENDING = "PENDING", "Pendente"
    PROCESSING = "PROCESSING", "Processando"
    COMPLETED = "COMPLETED", "Concluido"
    FAILED = "FAILED", "Falhou"


class ManualRefreshRequest(models.Model):
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="manual_refresh_requests",
    )
    bot = models.ForeignKey(
        ManagedBot,
        on_delete=models.CASCADE,
        related_name="manual_refresh_requests",
    )
    collection_job = models.ForeignKey(
        "monitoring.CollectionJob",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="manual_refresh_requests",
    )
    requested_by_telegram_user_id = models.BigIntegerField()
    status = models.CharField(
        max_length=16,
        choices=ManualRefreshStatus.choices,
        default=ManualRefreshStatus.PENDING,
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-requested_at",)
        indexes = [models.Index(fields=("tenant", "requested_at"))]


class BotSecurityEvent(models.Model):
    tenant = models.ForeignKey(
        Tenant,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="bot_security_events",
    )
    bot = models.ForeignKey(
        ManagedBot,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="security_events",
    )
    event_code = models.CharField(max_length=80)
    context = models.CharField(max_length=160, blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-occurred_at",)
