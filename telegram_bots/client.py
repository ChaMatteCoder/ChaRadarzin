from __future__ import annotations

from typing import Any

import requests
from django.conf import settings


class TelegramApiError(RuntimeError):
    def __init__(self, method: str, error_code: int | None = None) -> None:
        self.method = method
        self.error_code = error_code
        super().__init__(f"Telegram Bot API falhou em {method} ({error_code or 'sem codigo'}).")


class TelegramTransportError(RuntimeError):
    def __init__(self, method: str) -> None:
        self.method = method
        super().__init__(f"Falha de transporte ao chamar {method}.")


class TelegramApiClient:
    def __init__(
        self,
        token: str,
        *,
        timeout: float | None = None,
        session: requests.Session | None = None,
        api_base_url: str = "https://api.telegram.org",
    ) -> None:
        if not token or any(character.isspace() for character in token):
            raise ValueError("Token Telegram ausente ou invalido.")
        self._token = token
        self._timeout = timeout or settings.TELEGRAM_API_TIMEOUT_SECONDS
        self._session = session or requests.Session()
        self._api_base_url = api_base_url.rstrip("/")

    def _call(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        url = f"{self._api_base_url}/bot{self._token}/{method}"
        try:
            response = self._session.post(
                url,
                json=payload or {},
                timeout=self._timeout,
            )
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise TelegramTransportError(method) from exc
        if response.status_code >= 400 or not data.get("ok"):
            raw_error_code = data.get("error_code")
            error_code = int(raw_error_code) if isinstance(raw_error_code, int) else None
            raise TelegramApiError(method, error_code)
        return data.get("result")

    def get_me(self) -> dict[str, Any]:
        result = self._call("getMe")
        if not isinstance(result, dict):
            raise TelegramApiError("getMe")
        return result

    def set_webhook(
        self,
        url: str,
        secret_token: str,
        *,
        allowed_updates: list[str],
        drop_pending_updates: bool = False,
    ) -> bool:
        return bool(
            self._call(
                "setWebhook",
                {
                    "url": url,
                    "secret_token": secret_token,
                    "allowed_updates": allowed_updates,
                    "drop_pending_updates": drop_pending_updates,
                },
            )
        )

    def delete_webhook(self, *, drop_pending_updates: bool = False) -> bool:
        return bool(
            self._call(
                "deleteWebhook",
                {"drop_pending_updates": drop_pending_updates},
            )
        )

    def get_webhook_info(self) -> dict[str, Any]:
        result = self._call("getWebhookInfo")
        if not isinstance(result, dict):
            raise TelegramApiError("getWebhookInfo")
        return result

    def set_my_commands(self, commands: list[dict[str, str]]) -> bool:
        return bool(self._call("setMyCommands", {"commands": commands}))

    def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        result = self._call("sendMessage", payload)
        if not isinstance(result, dict):
            raise TelegramApiError("sendMessage")
        return result

    def answer_callback_query(self, callback_query_id: str, text: str = "") -> bool:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        return bool(self._call("answerCallbackQuery", payload))


class ManagerBotClient(TelegramApiClient):
    def get_managed_bot_token(self, bot_user_id: int) -> str:
        result = self._call("getManagedBotToken", {"user_id": bot_user_id})
        if not isinstance(result, str) or not result:
            raise TelegramApiError("getManagedBotToken")
        return result

    def replace_managed_bot_token(self, bot_user_id: int) -> str:
        result = self._call("replaceManagedBotToken", {"user_id": bot_user_id})
        if not isinstance(result, str) or not result:
            raise TelegramApiError("replaceManagedBotToken")
        return result

    def get_managed_bot_access_settings(self, bot_user_id: int) -> dict[str, Any]:
        result = self._call("getManagedBotAccessSettings", {"user_id": bot_user_id})
        if not isinstance(result, dict):
            raise TelegramApiError("getManagedBotAccessSettings")
        return result

    def restrict_managed_bot_to_owner(self, bot_user_id: int) -> bool:
        return bool(
            self._call(
                "setManagedBotAccessSettings",
                {
                    "user_id": bot_user_id,
                    "is_access_restricted": True,
                    "added_user_ids": [],
                },
            )
        )
