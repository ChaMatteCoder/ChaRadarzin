from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from tenancy.models import BetaAccessGrant, TelegramIdentity, Tenant, User


class TelegramIdentityError(ValueError):
    """Raised when validated OIDC claims conflict with a stored identity."""


@dataclass(frozen=True, slots=True)
class TelegramClaims:
    issuer: str
    subject: str
    telegram_user_id: int
    display_name: str
    username: str = ""
    picture_url: str = ""

    @classmethod
    def from_mapping(cls, claims: Mapping[str, object]) -> "TelegramClaims":
        issuer = str(claims.get("iss", "")).strip()
        subject = str(claims.get("sub", "")).strip()
        raw_telegram_user_id = claims.get("id")
        if issuer != settings.TELEGRAM_OIDC_ISSUER:
            raise TelegramIdentityError("Emissor OIDC do Telegram invalido.")
        if not subject:
            raise TelegramIdentityError("Claim OIDC sub ausente.")
        try:
            telegram_user_id = int(str(raw_telegram_user_id))
        except (TypeError, ValueError) as exc:
            raise TelegramIdentityError("Telegram User ID ausente ou invalido.") from exc
        if telegram_user_id <= 0:
            raise TelegramIdentityError("Telegram User ID deve ser positivo.")

        username = str(claims.get("preferred_username", "")).strip().lstrip("@")
        display_name = str(claims.get("name", "")).strip()
        if not display_name:
            display_name = f"@{username}" if username else f"Telegram {telegram_user_id}"
        return cls(
            issuer=issuer,
            subject=subject,
            telegram_user_id=telegram_user_id,
            display_name=display_name[:160],
            username=username[:64],
            picture_url=str(claims.get("picture", "")).strip()[:500],
        )


@transaction.atomic
def login_or_create_telegram_user(claims: TelegramClaims) -> User:
    beta_grant = None
    if settings.BETA_ACCESS_REQUIRED:
        beta_grant = (
            BetaAccessGrant.objects.select_for_update()
            .filter(telegram_user_id=claims.telegram_user_id, active=True)
            .first()
        )
        if beta_grant is None:
            raise TelegramIdentityError("Esta conta ainda nao participa do beta fechado.")

    identity = (
        TelegramIdentity.objects.select_for_update(of=("self",))
        .select_related("user", "user__tenant")
        .filter(issuer=claims.issuer, subject=claims.subject)
        .first()
    )
    if identity is not None:
        if identity.telegram_user_id != claims.telegram_user_id:
            raise TelegramIdentityError(
                "A identidade OIDC ja existe com outro Telegram User ID."
            )
        changed_fields: list[str] = []
        for field_name, value in (
            ("display_name", claims.display_name),
            ("telegram_username", claims.username),
            ("picture_url", claims.picture_url),
        ):
            if getattr(identity, field_name) != value:
                setattr(identity, field_name, value)
                changed_fields.append(field_name)
        if changed_fields:
            identity.save(update_fields=changed_fields + ["updated_at"])
        user = identity.user
        if beta_grant is not None and beta_grant.redeemed_by_id != user.pk:
            beta_grant.redeemed_by = user
            beta_grant.redeemed_at = timezone.now()
            beta_grant.save(update_fields=["redeemed_by", "redeemed_at", "updated_at"])
        return user

    if TelegramIdentity.objects.filter(
        telegram_user_id=claims.telegram_user_id
    ).exists():
        raise TelegramIdentityError(
            "Telegram User ID ja associado a outra identidade OIDC."
        )

    tenant = Tenant.objects.create(name=f"Radar de {claims.display_name}"[:120])
    username = f"telegram_{claims.telegram_user_id}"
    try:
        user = User.objects.create_user(username=username, tenant=tenant, password=None)
        TelegramIdentity.objects.create(
            user=user,
            issuer=claims.issuer,
            subject=claims.subject,
            telegram_user_id=claims.telegram_user_id,
            display_name=claims.display_name,
            telegram_username=claims.username,
            picture_url=claims.picture_url,
        )
        if beta_grant is not None:
            beta_grant.redeemed_by = user
            beta_grant.redeemed_at = timezone.now()
            beta_grant.save(update_fields=["redeemed_by", "redeemed_at", "updated_at"])
    except IntegrityError as exc:
        raise TelegramIdentityError(
            "Nao foi possivel criar uma identidade Telegram exclusiva."
        ) from exc
    return user
