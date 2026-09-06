from __future__ import annotations

import uuid

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q


postal_code_validator = RegexValidator(
    regex=r"^\d{8}$",
    message="Informe somente os oito digitos do CEP.",
)


class Tenant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    alerting_not_before = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("created_at",)

    def __str__(self) -> str:
        return self.name


class User(AbstractUser):
    tenant = models.ForeignKey(
        Tenant,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="users",
    )

    class Meta(AbstractUser.Meta):
        constraints = [
            models.CheckConstraint(
                condition=Q(is_staff=True) | Q(tenant__isnull=False),
                name="user_staff_or_has_tenant",
            )
        ]

    def clean(self) -> None:
        super().clean()
        if not self.is_staff and self.tenant_id is None:
            raise ValidationError({"tenant": "Usuarios comuns exigem um tenant."})


class TelegramIdentity(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="telegram_identity",
    )
    issuer = models.URLField(max_length=255)
    subject = models.CharField(max_length=255)
    telegram_user_id = models.BigIntegerField(unique=True)
    display_name = models.CharField(max_length=160)
    telegram_username = models.CharField(max_length=64, blank=True)
    picture_url = models.URLField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("issuer", "subject"),
                name="telegram_identity_issuer_subject_unique",
            )
        ]

    def __str__(self) -> str:
        return self.display_name


class BetaAccessGrant(models.Model):
    telegram_user_id = models.BigIntegerField(unique=True)
    telegram_username = models.CharField(max_length=64, blank=True)
    display_name = models.CharField(max_length=160, blank=True)
    active = models.BooleanField(default=True)
    redeemed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="beta_access_grants",
    )
    redeemed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return self.telegram_username or f"Telegram {self.telegram_user_id}"


class PaymentMethod(models.TextChoices):
    PIX = "PIX", "Pix"
    CARD = "CARD", "Cartao"
    BOLETO = "BOLETO", "Boleto"


class DeliveryProfile(models.Model):
    tenant = models.OneToOneField(
        Tenant,
        on_delete=models.CASCADE,
        related_name="delivery_profile",
    )
    postal_code = models.CharField(max_length=8, validators=[postal_code_validator])
    preferred_payment_method = models.CharField(
        max_length=16,
        choices=PaymentMethod.choices,
        default=PaymentMethod.PIX,
    )
    minimum_price_drop_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default="1.00",
    )
    alert_price_increase = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Perfil de entrega de {self.tenant}"
