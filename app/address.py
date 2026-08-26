from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.shipping import validate_postal_code


VIACEP_URL = "https://viacep.com.br/ws/{postal_code}/json/"


class AddressLookupError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Address:
    postal_code: str
    street: str
    district: str
    city: str
    state: str

    @property
    def formatted_postal_code(self) -> str:
        return f"{self.postal_code[:5]}-{self.postal_code[5:]}"

    @property
    def formatted(self) -> str:
        parts = [self.street, self.district, self.city]
        location = ", ".join(part for part in parts if part)
        if self.state:
            location = f"{location}/{self.state}" if location else self.state
        return f"{location} — CEP {self.formatted_postal_code}"


def parse_viacep_payload(payload: dict[str, Any], postal_code: str) -> Address:
    normalized = validate_postal_code(postal_code)
    if payload.get("erro") is True:
        raise AddressLookupError(f"CEP {normalized} nao encontrado")

    city = str(payload.get("localidade") or "").strip()
    state = str(payload.get("uf") or "").strip().upper()
    if not city or len(state) != 2:
        raise AddressLookupError("Resposta incompleta do servico de CEP")

    return Address(
        postal_code=normalized,
        street=str(payload.get("logradouro") or "").strip(),
        district=str(payload.get("bairro") or "").strip(),
        city=city,
        state=state,
    )


def resolve_postal_code(
    postal_code: str,
    *,
    timeout: float = 20.0,
    retries: int = 1,
) -> Address:
    normalized = validate_postal_code(postal_code)
    request = Request(
        VIACEP_URL.format(postal_code=normalized),
        headers={"Accept": "application/json", "User-Agent": "radar-precos/1.0"},
    )
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise AddressLookupError("Resposta inesperada do servico de CEP")
            return parse_viacep_payload(payload, normalized)
        except AddressLookupError:
            raise
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(0.4 * (attempt + 1))
    raise AddressLookupError(f"Falha ao consultar o CEP: {last_error}")
