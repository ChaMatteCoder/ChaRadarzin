from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path

from app.shipping import validate_postal_code


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True, slots=True)
class Settings:
    project_root: Path = PROJECT_ROOT
    input_file: Path = PROJECT_ROOT / "produtos.xlsx"
    database_file: Path = PROJECT_ROOT / "data" / "radar_precos.db"
    reports_dir: Path = PROJECT_ROOT / "reports"
    logs_dir: Path = PROJECT_ROOT / "logs"
    destination_postal_code: str | None = None
    request_timeout_seconds: float = 20.0
    request_retries: int = 1
    telegram_notifications_enabled: bool = False
    telegram_alert_price_increase: bool = False
    telegram_bot_token: str | None = field(default=None, repr=False)
    telegram_chat_id: str | None = field(default=None, repr=False)

    def ensure_directories(self) -> None:
        self.database_file.parent.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)


def _env_file_values(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _enabled(value: str) -> bool:
    return value.strip().casefold() in {"1", "true", "sim", "yes", "on"}


def get_settings(input_file: str | Path | None = None) -> Settings:
    env_file = _env_file_values(PROJECT_ROOT / ".env")
    raw_postal_code = os.environ.get("CEP_ENTREGA", env_file.get("CEP_ENTREGA", "")).strip()
    destination_postal_code = None
    if raw_postal_code and raw_postal_code != "00000000":
        destination_postal_code = validate_postal_code(raw_postal_code)

    timeout = float(
        os.environ.get("RADAR_TIMEOUT_SEGUNDOS", env_file.get("RADAR_TIMEOUT_SEGUNDOS", "20"))
    )
    retries = int(os.environ.get("RADAR_TENTATIVAS", env_file.get("RADAR_TENTATIVAS", "1")))
    telegram_enabled = _enabled(
        os.environ.get(
            "TELEGRAM_NOTIFICACOES",
            env_file.get("TELEGRAM_NOTIFICACOES", "nao"),
        )
    )
    telegram_alert_price_increase = _enabled(
        os.environ.get(
            "TELEGRAM_ALERTAR_AUMENTO",
            env_file.get("TELEGRAM_ALERTAR_AUMENTO", "nao"),
        )
    )
    telegram_bot_token = os.environ.get(
        "TELEGRAM_BOT_TOKEN", env_file.get("TELEGRAM_BOT_TOKEN", "")
    ).strip() or None
    telegram_chat_id = os.environ.get(
        "TELEGRAM_CHAT_ID", env_file.get("TELEGRAM_CHAT_ID", "")
    ).strip() or None
    if telegram_enabled and (telegram_bot_token is None or telegram_chat_id is None):
        raise ValueError(
            "TELEGRAM_NOTIFICACOES=sim exige TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID"
        )

    if input_file is None:
        return Settings(
            destination_postal_code=destination_postal_code,
            request_timeout_seconds=timeout,
            request_retries=retries,
            telegram_notifications_enabled=telegram_enabled,
            telegram_alert_price_increase=telegram_alert_price_increase,
            telegram_bot_token=telegram_bot_token,
            telegram_chat_id=telegram_chat_id,
        )

    resolved_input = Path(input_file)
    if not resolved_input.is_absolute():
        resolved_input = (PROJECT_ROOT / resolved_input).resolve()
    return Settings(
        input_file=resolved_input,
        destination_postal_code=destination_postal_code,
        request_timeout_seconds=timeout,
        request_retries=retries,
        telegram_notifications_enabled=telegram_enabled,
        telegram_alert_price_increase=telegram_alert_price_increase,
        telegram_bot_token=telegram_bot_token,
        telegram_chat_id=telegram_chat_id,
    )
