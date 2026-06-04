from __future__ import annotations

import json
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from dfba_client.types import INT64_MAX, UINT32_MAX, MarketInfo

INT16_MIN = -(2**15)
INT16_MAX = 2**15 - 1


@dataclass(frozen=True, slots=True)
class SymbologySnapshot:
    version: int
    markets: dict[int, MarketInfo]


def derive_symbology_url(ws_url: str) -> str:
    parsed = urlparse(ws_url)
    scheme = "https" if parsed.scheme == "wss" else "http"
    netloc = parsed.netloc
    return f"{scheme}://{netloc}/v1/symbology"


def fetch_symbology_snapshot(url: str, timeout_seconds: float = 5.0) -> SymbologySnapshot:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
    return parse_symbology_snapshot(body)


def parse_symbology_snapshot(payload: str) -> SymbologySnapshot:
    raw = json.loads(payload)
    if isinstance(raw, list):
        markets = _parse_markets(raw)
        return SymbologySnapshot(version=0, markets=markets)
    if not isinstance(raw, Mapping):
        raise ValueError("symbology response root must be an object or array")
    version = _optional_int(raw, "version", 0, INT64_MAX) or 0
    raw_markets = raw.get("markets")
    if not isinstance(raw_markets, list):
        raise ValueError("symbology response missing markets")
    markets = _parse_markets(raw_markets)
    return SymbologySnapshot(version=version, markets=markets)


def _parse_markets(raw_markets: list[Any]) -> dict[int, MarketInfo]:
    markets: dict[int, MarketInfo] = {}
    for item in raw_markets:
        if not isinstance(item, Mapping):
            raise ValueError("symbology response contained an invalid market")
        market = _parse_market(item)
        markets[market.feed_id] = market
    if len(markets) == 0:
        raise ValueError("symbology response contained no markets")
    return markets


def _parse_market(raw: Mapping[str, Any]) -> MarketInfo:
    feed_id = _required_int(raw, "id", 1, UINT32_MAX)
    symbol = _required_str(raw, "symbol")
    quote = _required_str(raw, "quote")
    px_exponent = _required_int(raw, "px_exponent", INT16_MIN, INT16_MAX)
    size_decimals = _required_int(raw, "size_decimals", 0, 255)
    status = _optional_str(raw, "status")
    if status is None:
        active = raw.get("active")
        status = "inactive" if active is False else "active"
    return MarketInfo(
        feed_id=feed_id,
        symbol=symbol,
        quote=quote,
        px_exponent=px_exponent,
        size_decimals=size_decimals,
        status=status,
    )


def _required_str(raw: Mapping[str, Any], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or value == "":
        raise ValueError(f"market field {field} must be a non-empty string")
    return value


def _optional_str(raw: Mapping[str, Any], field: str) -> str | None:
    value = raw.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"market field {field} must be a string")
    return value


def _required_int(raw: Mapping[str, Any], field: str, low: int, high: int) -> int:
    value = _optional_int(raw, field, low, high)
    if value is None:
        raise ValueError(f"market field {field} must be an integer")
    return value


def _optional_int(raw: Mapping[str, Any], field: str, low: int, high: int) -> int | None:
    value = raw.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"market field {field} must be an integer")
    if value < low or value > high:
        raise ValueError(f"market field {field} out of range")
    return value
