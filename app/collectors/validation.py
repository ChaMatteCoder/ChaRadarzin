from __future__ import annotations

import re

from app.collectors.common import compact, normalize


def model_matches(exact_model: str, title: str) -> bool:
    expected = compact(exact_model)
    return bool(expected) and expected in compact(title)


def variant_matches(variant: str, title: str) -> bool:
    normalized_title = normalize(title)
    compact_title = compact(title)
    for raw_part in variant.split("/"):
        part = raw_part.strip()
        if not part:
            continue
        compact_part = compact(part)
        if compact_part and compact_part in compact_title:
            continue
        normalized_part = normalize(part)
        if "polegada" in normalized_part:
            number = re.search(r"\d+(?:[.,]\d+)?", normalized_part)
            if number and number.group(0).replace(",", ".") in normalized_title.replace(
                ",", "."
            ):
                continue
        tokens = [
            token
            for token in re.findall(r"[a-z0-9]+", normalized_part)
            if len(token) >= 2
        ]
        if tokens and all(token in normalized_title for token in tokens):
            continue
        return False
    return True


def seller_matches(expected: str, actual: str) -> bool:
    expected_value = normalize(expected)
    actual_value = normalize(actual)
    return bool(expected_value and actual_value) and (
        expected_value == actual_value
        or expected_value in actual_value
        or actual_value in expected_value
    )
