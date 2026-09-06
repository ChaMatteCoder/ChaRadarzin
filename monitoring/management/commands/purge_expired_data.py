from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from monitoring.models import ProductLinkPreview, ProductPreviewStatus
from telegram_bots.models import (
    BotOnboardingSession,
    BotSecurityEvent,
    ManualRefreshRequest,
    ManualRefreshStatus,
    OnboardingStatus,
    TelegramUpdateReceipt,
)


class Command(BaseCommand):
    help = "Remove dados tecnicos expirados conforme a politica de retencao."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--confirm", action="store_true")

    def handle(self, *args, **options) -> None:
        now = timezone.now()
        transient_cutoff = now - timedelta(days=settings.TRANSIENT_DATA_RETENTION_DAYS)
        security_cutoff = now - timedelta(days=settings.SECURITY_EVENT_RETENTION_DAYS)
        querysets = {
            "product_previews": ProductLinkPreview.objects.filter(
                updated_at__lt=transient_cutoff,
                status__in=[
                    ProductPreviewStatus.CONFIRMED,
                    ProductPreviewStatus.EXPIRED,
                    ProductPreviewStatus.FAILED,
                ],
            ),
            "onboarding_sessions": BotOnboardingSession.objects.filter(
                updated_at__lt=transient_cutoff,
                status__in=[
                    OnboardingStatus.ACTIVE,
                    OnboardingStatus.EXPIRED,
                    OnboardingStatus.ABANDONED,
                    OnboardingStatus.FAILED,
                ],
            ),
            "telegram_receipts": TelegramUpdateReceipt.objects.filter(
                received_at__lt=transient_cutoff,
            ),
            "manual_refreshes": ManualRefreshRequest.objects.filter(
                finished_at__lt=transient_cutoff,
                status__in=[ManualRefreshStatus.COMPLETED, ManualRefreshStatus.FAILED],
            ),
            "security_events": BotSecurityEvent.objects.filter(
                occurred_at__lt=security_cutoff,
            ),
        }
        counts = {name: queryset.count() for name, queryset in querysets.items()}
        action = "PURGE" if options["confirm"] else "DRY_RUN"
        self.stdout.write(f"retention_action={action}")
        for name, count in counts.items():
            self.stdout.write(f"{name}={count}")
        if not options["confirm"]:
            self.stdout.write("Nada foi excluido. Use --confirm para aplicar.")
            return
        with transaction.atomic():
            for queryset in querysets.values():
                queryset.delete()
        self.stdout.write(self.style.SUCCESS("Retencao aplicada com sucesso."))
