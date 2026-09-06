from django.urls import path

from telegram_bots.views import (
    bot_status,
    create_personal_bot,
    disconnect_personal_bot,
    manager_webhook,
    personal_bot_webhook,
)


urlpatterns = [
    path("painel/bot/", bot_status, name="bot-status"),
    path("painel/bot/criar/", create_personal_bot, name="bot-create"),
    path("painel/bot/desconectar/", disconnect_personal_bot, name="bot-disconnect"),
    path(
        "webhooks/telegram/manager/<uuid:endpoint_id>/",
        manager_webhook,
        name="manager-bot-webhook",
    ),
    path(
        "webhooks/telegram/bot/<uuid:webhook_id>/",
        personal_bot_webhook,
        name="personal-bot-webhook",
    ),
]
