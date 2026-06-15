from __future__ import annotations

from typing import Any


MILLION = 1_000_000
TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


def normalize_token_usage(raw_usage: Any) -> dict[str, int]:
    """Normalize SDK-specific usage payloads into one stable shape.

    The raw payload is still stored separately. This shape is used for DB
    queries and cost estimates.
    """
    raw = _usage_payload(raw_usage if isinstance(raw_usage, dict) else {})
    input_tokens = _int_field(raw, "input_tokens", "prompt_tokens")
    output_tokens = _int_field(raw, "output_tokens", "completion_tokens")

    details = _input_details(raw)
    top_level_cache_read = _field_present(raw, "cache_read_input_tokens")
    cache_read_input_tokens = _int_field(raw, "cache_read_input_tokens")
    cache_creation_input_tokens = _int_field(raw, "cache_creation_input_tokens")
    if not top_level_cache_read:
        cache_read_input_tokens = _int_value(
            details.get("cached_tokens")
            or details.get("cache_read_input_tokens")
            or raw.get("cached_input_tokens")
            or raw.get("cached_tokens")
        )

    total_tokens = _int_field(raw, "total_tokens")
    if total_tokens == 0:
        total_tokens = (
            input_tokens
            + output_tokens
            + cache_read_input_tokens
            + cache_creation_input_tokens
        )

    billable_input_tokens = input_tokens
    if cache_read_input_tokens:
        # Responses-style usage reports cached tokens inside input_tokens.
        billable_input_tokens = max(0, input_tokens - cache_read_input_tokens)

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cache_read_input_tokens,
        "cache_creation_input_tokens": cache_creation_input_tokens,
        "billable_input_tokens": billable_input_tokens,
        "total_tokens": total_tokens,
    }


def estimate_usage_cost(
    usage: dict[str, int],
    *,
    input_price_per_1m: float | None,
    output_price_per_1m: float | None,
    cache_read_price_per_1m: float | None,
    cache_creation_price_per_1m: float | None,
) -> float | None:
    if (
        input_price_per_1m is None
        and output_price_per_1m is None
        and cache_read_price_per_1m is None
        and cache_creation_price_per_1m is None
    ):
        return None

    cost = 0.0
    if input_price_per_1m is not None:
        cost += (
            usage.get("billable_input_tokens", usage.get("input_tokens", 0))
            * input_price_per_1m
            / MILLION
        )

    cache_creation_price = (
        cache_creation_price_per_1m
        if cache_creation_price_per_1m is not None
        else input_price_per_1m
    )
    if cache_creation_price is not None:
        cost += usage.get("cache_creation_input_tokens", 0) * cache_creation_price / MILLION

    cache_read_price = (
        cache_read_price_per_1m
        if cache_read_price_per_1m is not None
        else input_price_per_1m
    )
    if cache_read_price is not None:
        cost += usage.get("cache_read_input_tokens", 0) * cache_read_price / MILLION

    if output_price_per_1m is not None:
        cost += usage.get("output_tokens", 0) * output_price_per_1m / MILLION

    return cost


def _usage_payload(raw: dict[str, Any]) -> dict[str, Any]:
    for key in ("total", "usage"):
        value = raw.get(key)
        if isinstance(value, dict):
            return value
    return raw


def _input_details(raw: dict[str, Any]) -> dict[str, Any]:
    for key in ("input_tokens_details", "input_token_details", "input_details"):
        value = raw.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _field_present(raw: dict[str, Any], key: str) -> bool:
    return raw.get(key) is not None


def _int_field(raw: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = raw.get(key)
        if value is not None:
            return _int_value(value)
    return 0


def _int_value(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0
