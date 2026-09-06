from django.contrib import admin

from telegram_bots.models import (
    BotOnboardingSession,
    BotSecurityEvent,
    ManagedBot,
    ManualRefreshRequest,
    TelegramUpdateReceipt,
)


@admin.register(ManagedBot)
class ManagedBotAdmin(admin.ModelAdmin):
    list_display = (
        "username",
        "tenant",
        "status",
        "credential_source",
        "access_restricted",
        "updated_at",
    )
    list_filter = ("status", "credential_source", "access_restricted")
    search_fields = ("username", "display_name", "tenant__name")
    readonly_fields = (
        "id",
        "telegram_bot_id",
        "owner_telegram_user_id",
        "webhook_public_id",
        "created_at",
        "updated_at",
        "token_rotated_at",
        "activated_at",
        "disconnected_at",
        "last_seen_at",
        "last_health_check_at",
    )
    exclude = ("token_ciphertext", "token_key_version", "webhook_secret_digest")


@admin.register(BotOnboardingSession)
class BotOnboardingSessionAdmin(admin.ModelAdmin):
    list_display = ("tenant", "status", "suggested_username", "expires_at", "is_open")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(TelegramUpdateReceipt)
class TelegramUpdateReceiptAdmin(admin.ModelAdmin):
    list_display = ("endpoint_key", "update_id", "update_kind", "status", "received_at")
    readonly_fields = [field.name for field in TelegramUpdateReceipt._meta.fields]


admin.site.register(ManualRefreshRequest)
admin.site.register(BotSecurityEvent)
