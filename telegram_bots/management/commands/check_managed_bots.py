from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone

from telegram_bots.client import TelegramApiClient, TelegramApiError, TelegramTransportError
from telegram_bots.configuration import mark_bot_revoked
from telegram_bots.crypto import BotTokenCipher, SecretDecryptionError
from telegram_bots.models import ManagedBot, ManagedBotStatus


class Command(BaseCommand):
    help = "Verifica tokens e webhooks dos bots sem exibir credenciais."

    def handle(self, *args, **options):
        checked = revoked = errors = 0
        for bot in ManagedBot.objects.exclude(status=ManagedBotStatus.DISCONNECTED):
            checked += 1
            try:
                token = BotTokenCipher.from_settings().decrypt(bot.token_ciphertext, bot.token_key_version)
                client = TelegramApiClient(token)
                me = client.get_me()
                info = client.get_webhook_info()
                expected_url = f"{settings.PUBLIC_BASE_URL}/webhooks/telegram/bot/{bot.webhook_public_id}/"
                if int(me.get("id", -1)) != bot.telegram_bot_id or info.get("url") != expected_url:
                    bot.status = ManagedBotStatus.WEBHOOK_ERROR
                    bot.last_error_code = "WEBHOOK_MISMATCH"
                elif bot.status in {ManagedBotStatus.ERROR, ManagedBotStatus.WEBHOOK_ERROR, ManagedBotStatus.REVOKED}:
                    bot.status = ManagedBotStatus.ACTIVE if bot.chat_id else ManagedBotStatus.AWAITING_START
                    bot.last_error_code = ""
                bot.last_health_check_at = timezone.now()
                bot.save(update_fields=["status", "last_error_code", "last_health_check_at", "updated_at"])
                self.stdout.write(f"{bot.username}: {bot.status}")
            except TelegramApiError as exc:
                if exc.error_code == 401:
                    mark_bot_revoked(bot)
                    revoked += 1
                    self.stdout.write(f"{bot.username}: REVOKED")
                else:
                    errors += 1
                    self.stdout.write(f"{bot.username}: API_ERROR")
            except (TelegramTransportError, SecretDecryptionError):
                errors += 1
                bot.last_health_check_at = timezone.now()
                bot.last_error_code = "HEALTH_CHECK_FAILED"
                bot.save(update_fields=["last_health_check_at", "last_error_code", "updated_at"])
                self.stdout.write(f"{bot.username}: CHECK_FAILED")
        self.stdout.write(f"checked={checked} revoked={revoked} errors={errors}")
