#!/usr/bin/env python3
"""
Telegram bot untuk cek harga crypto dan candle timeframe dari Binance.

Bot ini sengaja memakai Python standard library saja agar mudah dijalankan:
- Telegram Bot API via long polling
- Binance Spot public API untuk harga dan candlestick
"""

from __future__ import annotations

import json
import html
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import Any


TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}/{method}"
BINANCE_API_BASE = "https://api.binance.com"
COINGECKO_API_BASE = "https://api.coingecko.com/api/v3"
ETHERSCAN_API_BASE = "https://api.etherscan.io/v2/api"
DEXSCREENER_API_BASE = "https://api.dexscreener.com"
GOPLUS_API_BASE = "https://api.gopluslabs.io/api/v1"
ETH_RPC_URLS = (
    "https://ethereum.publicnode.com",
    "https://rpc.flashbots.net",
    "https://cloudflare-eth.com",
)
BINANCE_API_BASES = (
    "https://api.binance.com",
    "https://data-api.binance.vision",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
    "https://api4.binance.com",
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_VS_CURRENCIES = ("usd", "idr")
REQUEST_TIMEOUT_SECONDS = 20
POLL_TIMEOUT_SECONDS = 35
PRICE_CACHE_SECONDS = 5
KLINE_CACHE_SECONDS = 5
MARKET_STATS_CACHE_SECONDS = 30
GAS_CACHE_SECONDS = 15
CHART_CANDLE_LIMIT = 134
WEI_PER_GWEI = Decimal("1000000000")
TOKEN_MARKS_PATH = os.path.join(BASE_DIR, ".token_marks.json")
TOKEN_REFRESH_COOLDOWN_SECONDS = 5
TOKEN_CALLBACK_PREFIX = "tok"
GMGN_REFERRAL_ID = "30I510nA"

SUPPORTED_TOKEN_CHAINS = {
    "ethereum": {"tag": "ETH", "goplus_chain_id": "1", "gmgn_chain": "eth", "okx_chain": "ethereum"},
    "base": {"tag": "BASE", "goplus_chain_id": "8453", "gmgn_chain": "base", "okx_chain": "base"},
    "bsc": {"tag": "BNB", "goplus_chain_id": "56", "gmgn_chain": "bsc", "okx_chain": "bsc"},
}

BINANCE_TIMEFRAMES = (
    "1s",
    "1m",
    "3m",
    "5m",
    "15m",
    "30m",
    "1h",
    "2h",
    "4h",
    "6h",
    "8h",
    "12h",
    "1d",
    "3d",
    "1w",
    "1M",
)

TIMEFRAME_ALIASES = {
    "1mo": "1M",
    "1mon": "1M",
    "1month": "1M",
    "month": "1M",
    "monthly": "1M",
}

COMMON_COINS = {
    "btc": "BTC",
    "bitcoin": "BTC",
    "xbt": "BTC",
    "eth": "ETH",
    "ethereum": "ETH",
    "bnb": "BNB",
    "binancecoin": "BNB",
    "sol": "SOL",
    "solana": "SOL",
    "xrp": "XRP",
    "ripple": "XRP",
    "ada": "ADA",
    "cardano": "ADA",
    "doge": "DOGE",
    "dogecoin": "DOGE",
    "dot": "DOT",
    "polkadot": "DOT",
    "trx": "TRX",
    "tron": "TRX",
    "ltc": "LTC",
    "litecoin": "LTC",
    "bch": "BCH",
    "link": "LINK",
    "chainlink": "LINK",
    "avax": "AVAX",
    "avalanche": "AVAX",
    "ton": "TON",
    "toncoin": "TON",
    "shib": "SHIB",
    "shiba": "SHIB",
    "pepe": "PEPE",
    "usdt": "USDT",
    "tether": "USDT",
    "usdc": "USDC",
    "usd-coin": "USDC",
}

COINGECKO_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "BNB": "binancecoin",
    "SOL": "solana",
    "XRP": "ripple",
    "ADA": "cardano",
    "DOGE": "dogecoin",
    "DOT": "polkadot",
    "TRX": "tron",
    "LTC": "litecoin",
    "BCH": "bitcoin-cash",
    "LINK": "chainlink",
    "AVAX": "avalanche-2",
    "TON": "the-open-network",
    "SHIB": "shiba-inu",
    "PEPE": "pepe",
    "USDT": "tether",
    "USDC": "usd-coin",
}

HELP_TEXT = f"""Halo! Kirim command seperti ini:

/price btc
/price eth idr
/p btc
/mp btc sol eth
/gas
/ca 0xcontract
/convert 0.5 btc usd
/convert 250 doge idr
0.1 btc
0xcontract
/tv eth
/tv eth 15m
/kline btc 15m
/timeframes

Chart dikirim sebagai gambar candlestick.
Timeframe Binance yang tersedia:
{", ".join(BINANCE_TIMEFRAMES)}

Catatan: harga USD memakai pair USDT Binance, misalnya BTCUSDT.
"""


class BotError(Exception):
    """Error yang aman ditampilkan ke user Telegram."""


class SymbolUnavailable(BotError):
    """Pair Binance tidak tersedia."""


@dataclass
class PriceResult:
    asset: str
    prices: dict[str, Decimal]
    source_symbols: dict[str, str]
    updated_at: datetime


@dataclass
class CandleBar:
    open_time_ms: int
    close_time_ms: int
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: Decimal
    quote_volume: Decimal
    trades: int


@dataclass
class KlineResult:
    asset: str
    symbol: str
    interval: str
    open_time_ms: int
    close_time_ms: int
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: Decimal
    quote_volume: Decimal
    trades: int
    live_price: Decimal

    @property
    def change_percent(self) -> Decimal:
        if self.open_price == 0:
            return Decimal("0")
        return ((self.close_price - self.open_price) / self.open_price) * Decimal("100")


@dataclass
class ChartResult:
    asset: str
    symbol: str
    interval: str
    bars: list[CandleBar]
    live_price: Decimal


@dataclass
class PhotoReply:
    photo: bytes
    caption: str
    filename: str = "chart.png"


@dataclass
class TextReply:
    text: str
    parse_mode: str | None = None
    reply_markup: dict[str, Any] | None = None


@dataclass
class MarketStats:
    asset: str
    name: str
    price: Decimal
    btc_value: Decimal
    eth_value: Decimal
    high_24h: Decimal | None
    low_24h: Decimal | None
    change_1h: Decimal | None
    change_24h: Decimal | None
    change_7d: Decimal | None
    change_30d: Decimal | None
    ath: Decimal | None
    ath_change_percent: Decimal | None
    volume_24h: Decimal | None
    market_cap: Decimal | None
    updated_at: str | None


@dataclass
class GasEstimate:
    safe_gwei: Decimal
    standard_gwei: Decimal
    fast_gwei: Decimal
    base_fee_gwei: Decimal | None
    last_block: str | None
    gas_used_ratio: Decimal | None
    source: str


@dataclass
class TokenSecurity:
    buy_tax: Decimal | None = None
    sell_tax: Decimal | None = None
    is_honeypot: str | None = None
    is_open_source: str | None = None
    top_10_holder_rate: Decimal | None = None
    holder_count: str | None = None
    lp_holder_count: str | None = None


@dataclass
class TokenLink:
    label: str
    url: str


@dataclass
class TokenSnapshot:
    chain_id: str
    chain_tag: str
    address: str
    name: str
    symbol: str
    dex_id: str
    pair_address: str
    pair_url: str | None
    price_usd: Decimal | None
    market_cap: Decimal | None
    fdv: Decimal | None
    volume_24h: Decimal | None
    liquidity_usd: Decimal | None
    change_h1: Decimal | None
    change_h24: Decimal | None
    buys_h1: int | None
    sells_h1: int | None
    buys_h24: int | None
    sells_h24: int | None
    pair_created_at_ms: int | None
    websites: list[TokenLink]
    socials: list[TokenLink]
    security: TokenSecurity | None


@dataclass
class TokenMark:
    first_user: str
    first_seen: int
    first_market_cap: Decimal | None
    is_new: bool


class TokenMarkStore:
    def __init__(self, path: str = TOKEN_MARKS_PATH) -> None:
        self.path = path
        self._data = self.load()

    def load(self) -> dict[str, Any]:
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as marks_file:
                data = json.load(marks_file)
                return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as marks_file:
            json.dump(self._data, marks_file, ensure_ascii=False, indent=2, sort_keys=True)

    def get_or_create(
        self,
        chat_id: int,
        chain_id: str,
        address: str,
        user_label: str,
        market_cap: Decimal | None,
    ) -> TokenMark:
        key = f"{chat_id}:{chain_id}:{address.lower()}"
        existing = self._data.get(key)
        if isinstance(existing, dict):
            return TokenMark(
                first_user=str(existing.get("first_user") or "unknown"),
                first_seen=int(existing.get("first_seen") or int(time.time())),
                first_market_cap=decimal_from_any(existing.get("first_market_cap")),
                is_new=False,
            )

        now = int(time.time())
        self._data[key] = {
            "first_user": user_label,
            "first_seen": now,
            "first_market_cap": str(market_cap) if market_cap is not None else None,
        }
        self.save()
        return TokenMark(
            first_user=user_label,
            first_seen=now,
            first_market_cap=market_cap,
            is_new=True,
        )


class TokenLookupClient:
    def __init__(
        self,
        dexscreener_api_base: str = DEXSCREENER_API_BASE,
        goplus_api_base: str = GOPLUS_API_BASE,
    ) -> None:
        self.dexscreener_api_base = dexscreener_api_base.rstrip("/")
        self.goplus_api_base = goplus_api_base.rstrip("/")

    def get_token_snapshot(self, address: str, chain_id: str | None = None) -> TokenSnapshot:
        normalized = normalize_contract_address(address)
        if chain_id is not None and chain_id not in SUPPORTED_TOKEN_CHAINS:
            raise BotError("Chain token tidak didukung.")

        pairs: list[dict[str, Any]] = []
        chain_ids = (chain_id,) if chain_id else tuple(SUPPORTED_TOKEN_CHAINS)
        for current_chain_id in chain_ids:
            endpoint = f"{self.dexscreener_api_base}/tokens/v1/{current_chain_id}/{normalized}"
            try:
                payload = generic_get_json(endpoint)
            except BotError:
                continue
            if isinstance(payload, list):
                pairs.extend(pair for pair in payload if isinstance(pair, dict))

        candidates = [pair for pair in pairs if is_supported_token_pair(pair, normalized)]
        if not candidates:
            raise BotError("Contract address tidak ditemukan di DexScreener untuk ETH/Base/BNB.")

        best_pair = max(candidates, key=lambda pair: decimal_from_path(pair, ("liquidity", "usd")) or Decimal("0"))
        snapshot = self.build_snapshot(best_pair, normalized)
        snapshot.security = self.get_security(snapshot.chain_id, normalized)
        return snapshot

    def build_snapshot(self, pair: dict[str, Any], address: str) -> TokenSnapshot:
        chain_id = str(pair.get("chainId") or "")
        config = SUPPORTED_TOKEN_CHAINS.get(chain_id)
        if not config:
            raise BotError("Chain token tidak didukung.")

        base_token = pair.get("baseToken") if isinstance(pair.get("baseToken"), dict) else {}
        info = pair.get("info") if isinstance(pair.get("info"), dict) else {}
        websites = extract_websites(info)
        socials = extract_socials(info)
        txns_h1 = pair.get("txns", {}).get("h1", {}) if isinstance(pair.get("txns"), dict) else {}
        txns_h24 = pair.get("txns", {}).get("h24", {}) if isinstance(pair.get("txns"), dict) else {}

        return TokenSnapshot(
            chain_id=chain_id,
            chain_tag=str(config["tag"]),
            address=address,
            name=str(base_token.get("name") or "Unknown"),
            symbol=str(base_token.get("symbol") or "???"),
            dex_id=str(pair.get("dexId") or "dex"),
            pair_address=str(pair.get("pairAddress") or ""),
            pair_url=pair.get("url") if isinstance(pair.get("url"), str) else None,
            price_usd=decimal_from_any(pair.get("priceUsd")),
            market_cap=decimal_from_any(pair.get("marketCap")),
            fdv=decimal_from_any(pair.get("fdv")),
            volume_24h=decimal_from_path(pair, ("volume", "h24")),
            liquidity_usd=decimal_from_path(pair, ("liquidity", "usd")),
            change_h1=decimal_from_path(pair, ("priceChange", "h1")),
            change_h24=decimal_from_path(pair, ("priceChange", "h24")),
            buys_h1=int_or_none(txns_h1.get("buys")),
            sells_h1=int_or_none(txns_h1.get("sells")),
            buys_h24=int_or_none(txns_h24.get("buys")),
            sells_h24=int_or_none(txns_h24.get("sells")),
            pair_created_at_ms=int_or_none(pair.get("pairCreatedAt")),
            websites=websites,
            socials=socials,
            security=None,
        )

    def get_security(self, chain_id: str, address: str) -> TokenSecurity | None:
        config = SUPPORTED_TOKEN_CHAINS.get(chain_id)
        if not config:
            return None

        endpoint = f"{self.goplus_api_base}/token_security/{config['goplus_chain_id']}"
        try:
            payload = generic_get_json(endpoint, {"contract_addresses": address})
        except BotError:
            return None
        if not isinstance(payload, dict):
            return None

        result = payload.get("result")
        if not isinstance(result, dict):
            return None
        token_data = result.get(address.lower()) or result.get(address) or next(iter(result.values()), None)
        if not isinstance(token_data, dict):
            return None

        return TokenSecurity(
            buy_tax=percent_decimal_from_ratio(token_data.get("buy_tax")),
            sell_tax=percent_decimal_from_ratio(token_data.get("sell_tax")),
            is_honeypot=value_to_yes_no(token_data.get("is_honeypot")),
            is_open_source=value_to_yes_no(token_data.get("is_open_source")),
            top_10_holder_rate=percent_decimal_from_ratio(token_data.get("top_10_holder_rate")),
            holder_count=str(token_data.get("holder_count")) if token_data.get("holder_count") is not None else None,
            lp_holder_count=str(token_data.get("lp_holder_count")) if token_data.get("lp_holder_count") is not None else None,
        )


class GasClient:
    def __init__(
        self,
        etherscan_api_key: str | None = None,
        etherscan_api_base: str = ETHERSCAN_API_BASE,
        rpc_urls: tuple[str, ...] = ETH_RPC_URLS,
    ) -> None:
        self.etherscan_api_key = etherscan_api_key.strip() if etherscan_api_key else None
        self.etherscan_api_base = etherscan_api_base.rstrip("/")
        self.rpc_urls = tuple(url.rstrip("/") for url in rpc_urls if url.strip()) or ETH_RPC_URLS
        self._cache: tuple[float, GasEstimate] | None = None

    def get_gas(self) -> GasEstimate:
        now = time.time()
        if self._cache and now - self._cache[0] <= GAS_CACHE_SECONDS:
            return self._cache[1]

        errors: list[str] = []
        if self.etherscan_api_key:
            try:
                estimate = self.get_etherscan_gas()
                self._cache = (now, estimate)
                return estimate
            except BotError as exc:
                errors.append(str(exc))

        try:
            estimate = self.get_rpc_gas()
            self._cache = (now, estimate)
            return estimate
        except BotError as exc:
            errors.append(str(exc))

        raise BotError("Gagal mengambil gas Ethereum: " + " | ".join(errors))

    def get_etherscan_gas(self) -> GasEstimate:
        payload = generic_get_json(
            self.etherscan_api_base,
            {
                "chainid": "1",
                "module": "gastracker",
                "action": "gasoracle",
                "apikey": self.etherscan_api_key,
            },
        )
        if not isinstance(payload, dict) or payload.get("status") != "1":
            message = payload.get("message") if isinstance(payload, dict) else "response tidak valid"
            raise BotError(f"Etherscan gas oracle gagal: {message}")

        result = payload.get("result")
        if not isinstance(result, dict):
            raise BotError("Etherscan gas oracle response tidak valid.")

        safe = decimal_from_payload(result, "SafeGasPrice")
        standard = decimal_from_payload(result, "ProposeGasPrice")
        fast = decimal_from_payload(result, "FastGasPrice")
        if safe is None or standard is None or fast is None:
            raise BotError("Etherscan gas oracle tidak mengembalikan data gwei lengkap.")

        return GasEstimate(
            safe_gwei=safe,
            standard_gwei=standard,
            fast_gwei=fast,
            base_fee_gwei=decimal_from_payload(result, "suggestBaseFee"),
            last_block=str(result.get("LastBlock")) if result.get("LastBlock") is not None else None,
            gas_used_ratio=parse_latest_gas_used_ratio(result.get("gasUsedRatio")),
            source="Etherscan Gas Oracle",
        )

    def get_rpc_gas(self) -> GasEstimate:
        failures: list[str] = []
        for rpc_url in self.rpc_urls:
            try:
                payload = generic_post_json(
                    rpc_url,
                    {
                        "jsonrpc": "2.0",
                        "method": "eth_feeHistory",
                        "params": ["0x5", "latest", [10, 50, 90]],
                        "id": 1,
                    },
                )
                estimate = parse_fee_history(payload, rpc_url)
                return estimate
            except BotError as exc:
                failures.append(str(exc))

        raise BotError("Semua public Ethereum RPC gagal: " + " | ".join(failures))


class MarketStatsClient:
    def __init__(self, api_base: str = COINGECKO_API_BASE) -> None:
        self.api_base = api_base.rstrip("/")
        self._cache: dict[str, tuple[float, MarketStats]] = {}

    def resolve_coin_id(self, coin_text: str) -> tuple[str, str]:
        normalized = coin_text.strip().replace("$", "").replace("-", "").replace("_", "")
        if not normalized:
            raise BotError("Nama coin belum diisi. Contoh: /p btc")

        asset = COMMON_COINS.get(normalized.lower(), normalized.upper())
        coin_id = COINGECKO_IDS.get(asset)
        if not coin_id:
            coin_id = normalized.lower()
        return asset, coin_id

    def get_stats(self, coin_text: str) -> MarketStats:
        asset, coin_id = self.resolve_coin_id(coin_text)
        cached = self._cache.get(coin_id)
        now = time.time()
        if cached and now - cached[0] <= MARKET_STATS_CACHE_SECONDS:
            return cached[1]

        ids = sorted({coin_id, "bitcoin", "ethereum"})
        payload = self.coingecko_get_json(
            "/coins/markets",
            {
                "vs_currency": "usd",
                "ids": ",".join(ids),
                "order": "market_cap_desc",
                "per_page": str(len(ids)),
                "page": "1",
                "sparkline": "false",
                "price_change_percentage": "1h,24h,7d,30d",
                "locale": "en",
                "precision": "full",
            },
        )
        if not isinstance(payload, list) or not payload:
            raise BotError(f"Coin '{coin_text}' tidak ditemukan di CoinGecko.")

        by_id = {item.get("id"): item for item in payload if isinstance(item, dict)}
        target = by_id.get(coin_id)
        bitcoin = by_id.get("bitcoin")
        ethereum = by_id.get("ethereum")
        if not target:
            raise BotError(f"Coin '{coin_text}' tidak ditemukan di CoinGecko.")
        if not bitcoin or not ethereum:
            raise BotError("Data pembanding BTC/ETH belum tersedia dari CoinGecko.")

        price = decimal_from_payload(target, "current_price")
        btc_price = decimal_from_payload(bitcoin, "current_price")
        eth_price = decimal_from_payload(ethereum, "current_price")
        if price is None or btc_price in {None, Decimal("0")} or eth_price in {None, Decimal("0")}:
            raise BotError("Data harga belum lengkap dari CoinGecko.")

        stats = MarketStats(
            asset=(target.get("symbol") or asset).upper(),
            name=str(target.get("name") or asset),
            price=price,
            btc_value=price / btc_price,
            eth_value=price / eth_price,
            high_24h=decimal_from_payload(target, "high_24h"),
            low_24h=decimal_from_payload(target, "low_24h"),
            change_1h=decimal_from_payload(target, "price_change_percentage_1h_in_currency"),
            change_24h=decimal_from_payload(target, "price_change_percentage_24h_in_currency")
            or decimal_from_payload(target, "price_change_percentage_24h"),
            change_7d=decimal_from_payload(target, "price_change_percentage_7d_in_currency"),
            change_30d=decimal_from_payload(target, "price_change_percentage_30d_in_currency"),
            ath=decimal_from_payload(target, "ath"),
            ath_change_percent=decimal_from_payload(target, "ath_change_percentage"),
            volume_24h=decimal_from_payload(target, "total_volume"),
            market_cap=decimal_from_payload(target, "market_cap"),
            updated_at=target.get("last_updated") if isinstance(target.get("last_updated"), str) else None,
        )
        self._cache[coin_id] = (now, stats)
        return stats

    def get_multi_stats(self, coin_texts: list[str]) -> list[MarketStats]:
        requested: list[tuple[str, str]] = []
        seen_ids: set[str] = set()
        for coin_text in coin_texts:
            asset, coin_id = self.resolve_coin_id(coin_text)
            if coin_id in seen_ids:
                continue
            requested.append((asset, coin_id))
            seen_ids.add(coin_id)

        if not requested:
            raise BotError("Format: /mp btc sol eth")

        now = time.time()
        results: dict[str, MarketStats] = {}
        missing: list[tuple[str, str]] = []
        for asset, coin_id in requested:
            cached = self._cache.get(coin_id)
            if cached and now - cached[0] <= MARKET_STATS_CACHE_SECONDS:
                results[coin_id] = cached[1]
            else:
                missing.append((asset, coin_id))

        if missing:
            ids = sorted({coin_id for _asset, coin_id in missing} | {"bitcoin", "ethereum"})
            payload = self.coingecko_get_json(
                "/coins/markets",
                {
                    "vs_currency": "usd",
                    "ids": ",".join(ids),
                    "order": "market_cap_desc",
                    "per_page": str(len(ids)),
                    "page": "1",
                    "sparkline": "false",
                    "price_change_percentage": "1h,24h,7d,30d",
                    "locale": "en",
                    "precision": "full",
                },
            )
            if not isinstance(payload, list) or not payload:
                raise BotError("Coin tidak ditemukan di CoinGecko.")

            by_id = {item.get("id"): item for item in payload if isinstance(item, dict)}
            bitcoin = by_id.get("bitcoin")
            ethereum = by_id.get("ethereum")
            if not bitcoin or not ethereum:
                raise BotError("Data pembanding BTC/ETH belum tersedia dari CoinGecko.")

            btc_price = decimal_from_payload(bitcoin, "current_price")
            eth_price = decimal_from_payload(ethereum, "current_price")
            if btc_price in {None, Decimal("0")} or eth_price in {None, Decimal("0")}:
                raise BotError("Data harga BTC/ETH belum lengkap dari CoinGecko.")

            for asset, coin_id in missing:
                target = by_id.get(coin_id)
                if not target:
                    raise BotError(f"Coin '{asset}' tidak ditemukan di CoinGecko.")
                stats = self.build_market_stats(asset, target, btc_price, eth_price)
                self._cache[coin_id] = (now, stats)
                results[coin_id] = stats

        return [results[coin_id] for _asset, coin_id in requested]

    def build_market_stats(
        self,
        fallback_asset: str,
        target: dict[str, Any],
        btc_price: Decimal,
        eth_price: Decimal,
    ) -> MarketStats:
        price = decimal_from_payload(target, "current_price")
        if price is None:
            raise BotError("Data harga belum lengkap dari CoinGecko.")

        return MarketStats(
            asset=(target.get("symbol") or fallback_asset).upper(),
            name=str(target.get("name") or fallback_asset),
            price=price,
            btc_value=price / btc_price,
            eth_value=price / eth_price,
            high_24h=decimal_from_payload(target, "high_24h"),
            low_24h=decimal_from_payload(target, "low_24h"),
            change_1h=decimal_from_payload(target, "price_change_percentage_1h_in_currency"),
            change_24h=decimal_from_payload(target, "price_change_percentage_24h_in_currency")
            or decimal_from_payload(target, "price_change_percentage_24h"),
            change_7d=decimal_from_payload(target, "price_change_percentage_7d_in_currency"),
            change_30d=decimal_from_payload(target, "price_change_percentage_30d_in_currency"),
            ath=decimal_from_payload(target, "ath"),
            ath_change_percent=decimal_from_payload(target, "ath_change_percentage"),
            volume_24h=decimal_from_payload(target, "total_volume"),
            market_cap=decimal_from_payload(target, "market_cap"),
            updated_at=target.get("last_updated") if isinstance(target.get("last_updated"), str) else None,
        )

    def coingecko_get_json(self, path: str, params: dict[str, Any]) -> Any:
        query = urllib.parse.urlencode(params)
        url = f"{self.api_base}{path}?{query}"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "crypto-telegram-converter/2.0",
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            message = parse_error_message(body) or str(exc)
            raise BotError(f"CoinGecko API error ({exc.code}): {message}") from exc


class BinanceMarketClient:
    def __init__(self, api_bases: tuple[str, ...] = BINANCE_API_BASES) -> None:
        self.api_bases = tuple(base.rstrip("/") for base in api_bases if base.strip())
        if not self.api_bases:
            self.api_bases = BINANCE_API_BASES
        self._price_cache: dict[str, tuple[float, Decimal]] = {}
        self._kline_cache: dict[tuple[str, str], tuple[float, KlineResult]] = {}

    def resolve_asset(self, coin_text: str) -> str:
        normalized = coin_text.strip().replace("$", "").replace("-", "").replace("_", "")
        if not normalized:
            raise BotError("Nama coin belum diisi. Contoh: /price btc")

        asset = COMMON_COINS.get(normalized.lower(), normalized.upper())
        if not re.fullmatch(r"[A-Z0-9]{2,20}", asset):
            raise BotError("Nama coin tidak valid. Contoh: btc, eth, sol, xrp.")
        return asset

    def build_symbol(self, coin_text: str, quote_asset: str = "USDT") -> tuple[str, str]:
        raw = coin_text.strip().replace("$", "").replace("-", "").replace("_", "").upper()
        if raw.endswith(quote_asset) and len(raw) > len(quote_asset):
            return raw[:-len(quote_asset)], raw

        asset = self.resolve_asset(coin_text)
        return asset, f"{asset}{quote_asset}"

    def get_prices(self, coin_text: str, vs_currencies: tuple[str, ...]) -> PriceResult:
        asset = self.resolve_asset(coin_text)
        prices: dict[str, Decimal] = {}
        source_symbols: dict[str, str] = {}

        for currency in vs_currencies:
            target = normalize_currency(currency)
            price, source_symbol = self.get_price_value(asset, target)
            prices[target] = price
            source_symbols[target] = source_symbol

        return PriceResult(
            asset=asset,
            prices=prices,
            source_symbols=source_symbols,
            updated_at=datetime.now(timezone.utc),
        )

    def get_price_value(self, asset: str, currency: str) -> tuple[Decimal, str]:
        if currency in {"usd", "usdt"}:
            if asset == "USDT":
                return Decimal("1"), "USDT"
            symbol = f"{asset}USDT"
            return self.get_symbol_price(symbol), symbol

        if currency == "idr":
            for symbol in (f"{asset}IDR", f"{asset}BIDR"):
                try:
                    return self.get_symbol_price(symbol), symbol
                except SymbolUnavailable:
                    continue

            try:
                crypto_usdt = Decimal("1") if asset == "USDT" else self.get_symbol_price(f"{asset}USDT")
                usdt_idr, rate_symbol = self.get_usdt_idr_rate()
            except SymbolUnavailable as exc:
                raise BotError(
                    "IDR belum tersedia dari Binance Spot untuk coin ini. "
                    "Coba target USD/USDT, misalnya /price btc usd."
                ) from exc

            return crypto_usdt * usdt_idr, f"{asset}USDT x {rate_symbol}"

        raise BotError("Target mata uang hanya mendukung USD, USDT, atau IDR.")

    def get_usdt_idr_rate(self) -> tuple[Decimal, str]:
        for symbol in ("USDTIDR", "USDTBIDR"):
            try:
                return self.get_symbol_price(symbol), symbol
            except SymbolUnavailable:
                continue
        raise SymbolUnavailable("Pair USDTIDR/USDTBIDR tidak tersedia di Binance Spot.")

    def get_symbol_price(self, symbol: str) -> Decimal:
        symbol = symbol.upper()
        cached = self._price_cache.get(symbol)
        now = time.time()
        if cached and now - cached[0] <= PRICE_CACHE_SECONDS:
            return cached[1]

        data = self.binance_get_json("/api/v3/ticker/price", {"symbol": symbol})
        if not isinstance(data, dict) or "price" not in data:
            raise SymbolUnavailable(f"Pair {symbol} tidak tersedia di Binance Spot.")

        price = Decimal(str(data["price"]))
        self._price_cache[symbol] = (now, price)
        return price

    def get_kline(self, coin_text: str, interval: str) -> KlineResult:
        interval = normalize_timeframe(interval)
        asset, symbol = self.build_symbol(coin_text, "USDT")

        cached = self._kline_cache.get((symbol, interval))
        now = time.time()
        if cached and now - cached[0] <= KLINE_CACHE_SECONDS:
            return cached[1]

        data = self.binance_get_json(
            "/api/v3/klines",
            {
                "symbol": symbol,
                "interval": interval,
                "limit": "1",
            },
        )
        if not isinstance(data, list) or not data:
            raise SymbolUnavailable(f"Candle {symbol} {interval} tidak tersedia di Binance Spot.")

        row = data[-1]
        if not isinstance(row, list) or len(row) < 9:
            raise BotError("Format response candle Binance tidak dikenali.")

        result = KlineResult(
            asset=asset,
            symbol=symbol,
            interval=interval,
            open_time_ms=int(row[0]),
            open_price=Decimal(str(row[1])),
            high_price=Decimal(str(row[2])),
            low_price=Decimal(str(row[3])),
            close_price=Decimal(str(row[4])),
            volume=Decimal(str(row[5])),
            close_time_ms=int(row[6]),
            quote_volume=Decimal(str(row[7])),
            trades=int(row[8]),
            live_price=self.get_symbol_price(symbol),
        )
        self._kline_cache[(symbol, interval)] = (now, result)
        return result

    def get_klines(self, coin_text: str, interval: str, limit: int = CHART_CANDLE_LIMIT) -> ChartResult:
        interval = normalize_timeframe(interval)
        asset, symbol = self.build_symbol(coin_text, "USDT")
        safe_limit = max(30, min(limit, 500))

        data = self.binance_get_json(
            "/api/v3/klines",
            {
                "symbol": symbol,
                "interval": interval,
                "limit": str(safe_limit),
            },
        )
        if not isinstance(data, list) or not data:
            raise SymbolUnavailable(f"Chart {symbol} {interval} tidak tersedia di Binance Spot.")

        bars: list[CandleBar] = []
        for row in data:
            if not isinstance(row, list) or len(row) < 9:
                raise BotError("Format response chart Binance tidak dikenali.")
            bars.append(
                CandleBar(
                    open_time_ms=int(row[0]),
                    open_price=Decimal(str(row[1])),
                    high_price=Decimal(str(row[2])),
                    low_price=Decimal(str(row[3])),
                    close_price=Decimal(str(row[4])),
                    volume=Decimal(str(row[5])),
                    close_time_ms=int(row[6]),
                    quote_volume=Decimal(str(row[7])),
                    trades=int(row[8]),
                )
            )

        return ChartResult(
            asset=asset,
            symbol=symbol,
            interval=interval,
            bars=bars,
            live_price=self.get_symbol_price(symbol),
        )

    def binance_get_json(self, path: str, params: dict[str, Any]) -> Any:
        query = urllib.parse.urlencode(params)
        failures: list[str] = []

        for api_base in self.api_bases:
            url = f"{api_base}{path}?{query}"
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "crypto-telegram-converter/2.0"},
            )

            try:
                with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                message = parse_error_message(body) or str(exc)
                if "Invalid symbol" in message or "-1121" in body:
                    raise SymbolUnavailable(message) from exc
                failures.append(f"{api_base} -> {exc.code}: {message}")
                if exc.code in {418, 429, 451, 500, 502, 503, 504}:
                    continue
                raise BotError(f"Binance API error ({exc.code}): {message}") from exc
            except urllib.error.URLError as exc:
                failures.append(f"{api_base} -> {exc.reason}")
                continue

        if any("451:" in failure for failure in failures):
            raise BotError(
                "Binance menolak request dari lokasi/IP VPS ini (HTTP 451 restricted location). "
                "Coba pindah region VPS, gunakan jaringan lain, atau set BINANCE_API_BASES ke endpoint Binance "
                "yang bisa diakses dari server kamu."
            )

        raise BotError("Semua endpoint Binance gagal diakses: " + " | ".join(failures))


def render_candlestick_chart(chart: ChartResult) -> bytes:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle
        from matplotlib.ticker import FuncFormatter
    except ModuleNotFoundError as exc:
        raise BotError(
            "Fitur chart membutuhkan matplotlib. Di Ubuntu/Debian baru, jalankan: "
            "sudo apt update && sudo apt install -y python3-matplotlib. "
            "Alternatif: pakai virtualenv lalu pip install -r requirements.txt."
        ) from exc

    bars = chart.bars
    if len(bars) < 2:
        raise BotError("Data candle belum cukup untuk membuat chart.")

    x_values = list(range(len(bars)))
    high = max(float(bar.high_price) for bar in bars)
    low = min(float(bar.low_price) for bar in bars)
    price_padding = max((high - low) * 0.08, high * 0.002)
    price_min = low - price_padding
    price_max = high + price_padding
    body_min_height = max((price_max - price_min) * 0.0012, 0.00000001)

    fig, (ax_price, ax_volume) = plt.subplots(
        2,
        1,
        figsize=(12.8, 7.2),
        dpi=100,
        sharex=True,
        gridspec_kw={"height_ratios": [4.3, 1], "hspace": 0.03},
    )
    fig.patch.set_facecolor("#050608")
    fig.subplots_adjust(left=0.04, right=0.925, top=0.87, bottom=0.09)

    for axis in (ax_price, ax_volume):
        axis.set_facecolor("#101214")
        axis.grid(True, color="#252a30", linewidth=0.65, alpha=0.55)
        axis.tick_params(colors="#9aa4af", labelsize=8)
        axis.yaxis.tick_right()
        for spine in axis.spines.values():
            spine.set_color("#20252b")

    candle_width = 0.58
    green = "#00b894"
    red = "#e64b5d"

    for index, bar in enumerate(bars):
        open_price = float(bar.open_price)
        high_price = float(bar.high_price)
        low_price = float(bar.low_price)
        close_price = float(bar.close_price)
        color = green if bar.close_price >= bar.open_price else red

        ax_price.vlines(index, low_price, high_price, color=color, linewidth=0.9, alpha=0.95)
        body_bottom = min(open_price, close_price)
        body_height = max(abs(close_price - open_price), body_min_height)
        ax_price.add_patch(
            Rectangle(
                (index - candle_width / 2, body_bottom),
                candle_width,
                body_height,
                facecolor=color,
                edgecolor=color,
                linewidth=0.7,
            )
        )
        ax_volume.bar(
            index,
            float(bar.volume),
            width=candle_width,
            color=color,
            alpha=0.5,
            linewidth=0,
        )

    last_bar = bars[-1]
    previous_close = bars[-2].close_price
    change = last_bar.close_price - previous_close
    change_percent = Decimal("0") if previous_close == 0 else (change / previous_close) * Decimal("100")
    sign = "+" if change >= 0 else ""
    change_color = green if change >= 0 else red

    ax_price.set_xlim(-1, len(bars))
    ax_price.set_ylim(price_min, price_max)
    ax_price.yaxis.set_major_formatter(FuncFormatter(lambda value, _pos: f"{value:,.2f}"))
    ax_price.axhline(float(last_bar.close_price), color=change_color, linestyle="--", linewidth=0.8, alpha=0.6)
    ax_price.text(
        1.006,
        float(last_bar.close_price),
        format_chart_price(last_bar.close_price),
        transform=ax_price.get_yaxis_transform(),
        va="center",
        ha="left",
        fontsize=8,
        color="#ffffff",
        bbox={"boxstyle": "round,pad=0.18", "facecolor": change_color, "edgecolor": change_color},
    )

    tick_positions = build_chart_ticks(len(bars), 8)
    ax_volume.set_xticks(tick_positions)
    ax_volume.set_xticklabels(
        [format_chart_time(bars[position].open_time_ms, chart.interval) for position in tick_positions],
        color="#9aa4af",
        fontsize=8,
    )
    ax_volume.set_yticks([])
    ax_price.tick_params(labelbottom=False)

    fig.text(0.045, 0.945, "CryptoWhale", color="#ff4d5a", fontsize=16, fontweight="bold", ha="left")
    fig.text(
        0.045,
        0.905,
        f"{chart.asset} / TetherUS - {chart.interval} - Binance",
        color="#e8edf2",
        fontsize=10,
        ha="left",
    )
    fig.text(
        0.255,
        0.905,
        (
            f"O {format_chart_price(last_bar.open_price)}  "
            f"H {format_chart_price(last_bar.high_price)}  "
            f"L {format_chart_price(last_bar.low_price)}  "
            f"C {format_chart_price(last_bar.close_price)}  "
            f"{sign}{format_decimal(change)} ({sign}{format_decimal(change_percent.quantize(Decimal('0.01')))}%)"
        ),
        color=change_color,
        fontsize=8,
        ha="left",
    )
    ax_volume.text(
        0.01,
        0.78,
        f"Vol {chart.asset} {format_decimal(last_bar.volume)}",
        transform=ax_volume.transAxes,
        color="#9aa4af",
        fontsize=8,
        ha="left",
    )
    fig.patches.append(
        Rectangle((0.01, 0.01), 0.98, 0.98, transform=fig.transFigure, fill=False, edgecolor="#00fff0", linewidth=3)
    )

    image = BytesIO()
    fig.savefig(image, format="png", facecolor=fig.get_facecolor(), bbox_inches="tight", pad_inches=0.06)
    plt.close(fig)
    return image.getvalue()


class TelegramBot:
    def __init__(
        self,
        token: str,
        price_client: BinanceMarketClient,
        stats_client: MarketStatsClient,
        gas_client: GasClient,
        token_client: TokenLookupClient,
        mark_store: TokenMarkStore,
    ) -> None:
        self.token = token
        self.price_client = price_client
        self.stats_client = stats_client
        self.gas_client = gas_client
        self.token_client = token_client
        self.mark_store = mark_store
        self.offset = load_offset()
        self.token_refresh_times: dict[tuple[int, int], float] = {}

    def run_forever(self) -> None:
        print("Bot aktif. Tekan Ctrl+C untuk berhenti.")
        while True:
            try:
                updates = self.get_updates()
                for update in updates:
                    self.offset = update["update_id"] + 1
                    save_offset(self.offset)
                    self.handle_update(update)
            except KeyboardInterrupt:
                print("\nBot berhenti.")
                return
            except urllib.error.URLError as exc:
                print(f"Koneksi bermasalah: {exc}. Mencoba lagi 5 detik...", file=sys.stderr)
                time.sleep(5)
            except Exception as exc:
                print(f"Error tak terduga: {exc}", file=sys.stderr)
                time.sleep(2)

    def get_updates(self) -> list[dict[str, Any]]:
        payload = {
            "timeout": POLL_TIMEOUT_SECONDS,
            "offset": self.offset,
            "allowed_updates": json.dumps(["message", "callback_query"]),
        }
        response = self.telegram_request("getUpdates", payload)
        return response.get("result", [])

    def handle_update(self, update: dict[str, Any]) -> None:
        if "callback_query" in update:
            self.handle_callback_query(update["callback_query"])
            return

        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        text = (message.get("text") or "").strip()

        if chat_id is None or not text:
            return
        if not text.startswith("/") and not is_quick_convert_amount_coin(text) and not extract_contract_address(text):
            return

        try:
            reply = self.build_reply(text, chat_id=chat_id, user_label=format_message_user(message))
        except BotError as exc:
            reply = f"{exc}\n\nKetik /help untuk contoh command."

        if isinstance(reply, PhotoReply):
            self.send_photo(chat_id, reply.photo, reply.caption, reply.filename)
        elif isinstance(reply, TextReply):
            self.send_message(chat_id, reply.text, parse_mode=reply.parse_mode, reply_markup=reply.reply_markup)
        else:
            self.send_message(chat_id, reply)

    def handle_callback_query(self, callback_query: dict[str, Any]) -> None:
        callback_id = str(callback_query.get("id") or "")
        try:
            callback = parse_token_callback_data(str(callback_query.get("data") or ""))
            message = callback_query.get("message") if isinstance(callback_query.get("message"), dict) else {}
            chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
            chat_id = chat.get("id")
            message_id = message.get("message_id")
            if chat_id is None or message_id is None:
                raise BotError("Pesan token tidak ditemukan.")

            if callback["action"] == "d":
                self.delete_message(chat_id, message_id)
                self.token_refresh_times.pop((int(chat_id), int(message_id)), None)
                self.answer_callback_query(callback_id, "Dihapus.")
                return

            if callback["action"] == "r":
                self.refresh_token_message(callback_query, callback, int(chat_id), int(message_id), message)
                return

            raise BotError("Action tombol tidak dikenal.")
        except BotError as exc:
            if callback_id:
                self.answer_callback_query(callback_id, str(exc), show_alert=True)

    def refresh_token_message(
        self,
        callback_query: dict[str, Any],
        callback: dict[str, str],
        chat_id: int,
        message_id: int,
        message: dict[str, Any],
    ) -> None:
        callback_id = str(callback_query.get("id") or "")
        now = time.time()
        refresh_key = (chat_id, message_id)
        last_refresh = self.token_refresh_times.get(refresh_key)
        if last_refresh is None:
            last_refresh = float(message.get("edit_date") or message.get("date") or 0)

        remaining = TOKEN_REFRESH_COOLDOWN_SECONDS - (now - last_refresh)
        if remaining > 0:
            wait_seconds = int(remaining) + 1
            self.answer_callback_query(callback_id, f"Tunggu {wait_seconds} detik sebelum refresh.")
            return

        snapshot = self.token_client.get_token_snapshot(callback["address"], chain_id=callback["chain_id"])
        mark = self.mark_store.get_or_create(
            chat_id=chat_id,
            chain_id=snapshot.chain_id,
            address=snapshot.address,
            user_label=format_callback_user(callback_query),
            market_cap=snapshot.market_cap or snapshot.fdv,
        )
        reply = build_token_text_reply(snapshot, mark)
        try:
            self.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=reply.text,
                parse_mode=reply.parse_mode,
                reply_markup=reply.reply_markup,
            )
        except BotError as exc:
            if "message is not modified" not in str(exc).lower():
                raise
            self.token_refresh_times[refresh_key] = now
            self.answer_callback_query(callback_id, "Data masih sama.")
            return

        self.token_refresh_times[refresh_key] = now
        self.answer_callback_query(callback_id, "Data diperbarui.")

    def build_reply(self, text: str, chat_id: int | None = None, user_label: str = "unknown") -> str | PhotoReply | TextReply:
        command = strip_bot_mention(text)
        lower = command.lower().strip()

        if lower in {"/start", "/help"}:
            return HELP_TEXT
        if lower in {"/timeframes", "/tf"}:
            return reply_timeframes()
        if lower == "/gas":
            return self.reply_gas()

        request = parse_user_request(command)
        if request["kind"] == "price":
            return self.reply_price(request["coin"], request["currency"])
        if request["kind"] == "stats":
            return self.reply_market_stats(request["coin"])
        if request["kind"] == "multi_stats":
            return self.reply_multi_market_stats(request["coins"])
        if request["kind"] == "convert":
            return self.reply_convert(request["amount"], request["coin"], request["currency"])
        if request["kind"] == "convert_default":
            return self.reply_convert_default(request["amount"], request["coin"])
        if request["kind"] == "kline":
            return self.reply_kline(request["coin"], request["timeframe"])
        if request["kind"] == "chart":
            return self.reply_chart(request["coin"], request["timeframe"])
        if request["kind"] == "token_lookup":
            if chat_id is None:
                raise BotError("Chat id tidak tersedia untuk mark contract.")
            return self.reply_token_lookup(request["address"], chat_id, user_label)

        raise BotError("Command belum dikenali.")

    def reply_price(self, coin: str, currency: str | None) -> str:
        targets = (currency.lower(),) if currency else DEFAULT_VS_CURRENCIES
        result = self.price_client.get_prices(coin, targets)

        lines = [f"Harga {result.asset} sekarang:"]
        for target in targets:
            price = result.prices.get(target)
            if price is None:
                continue
            source_symbol = result.source_symbols.get(target, "Binance")
            lines.append(f"- {target.upper()}: {format_money(price, target)} ({source_symbol})")

        lines.append(f"\nUpdate: {format_datetime(result.updated_at)}")
        lines.append("Sumber: Binance Spot")
        return "\n".join(lines)

    def reply_market_stats(self, coin: str) -> str:
        stats = self.stats_client.get_stats(coin)
        return format_market_stats(stats)

    def reply_multi_market_stats(self, coins: list[str]) -> str:
        stats = self.stats_client.get_multi_stats(coins)
        return format_multi_market_stats(stats)

    def reply_gas(self) -> str:
        return format_gas_estimate(self.gas_client.get_gas())

    def reply_convert(self, amount: Decimal, coin: str, currency: str) -> str:
        target = currency.lower()
        result = self.price_client.get_prices(coin, (target,))
        price = result.prices[target]
        total = amount * price

        lines = [
            f"{format_decimal(amount)} {result.asset} = {format_money(total, target)}",
            f"Harga 1 {result.asset}: {format_money(price, target)} ({result.source_symbols[target]})",
            f"Update: {format_datetime(result.updated_at)}",
            "Sumber: Binance Spot",
        ]
        return "\n".join(lines)

    def reply_convert_default(self, amount: Decimal, coin: str) -> str:
        result = self.price_client.get_prices(coin, ("usd", "idr"))

        lines = [f"{format_decimal(amount)} {result.asset} ="]
        for target in ("usd", "idr"):
            price = result.prices[target]
            total = amount * price
            lines.append(f"- {target.upper()}: {format_money(total, target)}")
        lines.append(f"Harga 1 {result.asset}: {format_money(result.prices['usd'], 'usd')} ({result.source_symbols['usd']})")
        lines.append(f"Update: {format_datetime(result.updated_at)}")
        lines.append("Sumber: Binance Spot")
        return "\n".join(lines)

    def reply_kline(self, coin: str, timeframe: str) -> str:
        result = self.price_client.get_kline(coin, timeframe)
        sign = "+" if result.change_percent >= 0 else ""

        return "\n".join(
            [
                f"{result.symbol} candle {result.interval}",
                f"Harga live: {format_quote_money(result.live_price, 'USDT')}",
                f"Open: {format_quote_money(result.open_price, 'USDT')}",
                f"High: {format_quote_money(result.high_price, 'USDT')}",
                f"Low: {format_quote_money(result.low_price, 'USDT')}",
                f"Close: {format_quote_money(result.close_price, 'USDT')}",
                f"Change: {sign}{format_decimal(result.change_percent.quantize(Decimal('0.01')))}%",
                f"Volume: {format_decimal(result.volume)} {result.asset}",
                f"Trades: {result.trades}",
                f"Open time: {format_ms_timestamp(result.open_time_ms)}",
                "Sumber: Binance Spot",
            ]
        )

    def reply_chart(self, coin: str, timeframe: str) -> PhotoReply:
        result = self.price_client.get_klines(coin, timeframe)
        photo = render_candlestick_chart(result)
        caption = f"CryptoWhale\n{result.symbol} {result.interval} - Binance Spot"
        return PhotoReply(photo=photo, caption=caption, filename=f"{result.symbol}_{result.interval}.png")

    def reply_token_lookup(self, address: str, chat_id: int, user_label: str) -> TextReply:
        snapshot = self.token_client.get_token_snapshot(address)
        mark = self.mark_store.get_or_create(
            chat_id=chat_id,
            chain_id=snapshot.chain_id,
            address=snapshot.address,
            user_label=user_label,
            market_cap=snapshot.market_cap or snapshot.fdv,
        )
        return build_token_text_reply(snapshot, mark)

    def send_message(
        self,
        chat_id: int,
        text: str,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)
        self.telegram_request(
            "sendMessage",
            payload,
        )

    def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)
        self.telegram_request("editMessageText", payload)

    def delete_message(self, chat_id: int, message_id: int) -> None:
        self.telegram_request("deleteMessage", {"chat_id": chat_id, "message_id": message_id})

    def answer_callback_query(self, callback_query_id: str, text: str | None = None, show_alert: bool = False) -> None:
        if not callback_query_id:
            return
        payload: dict[str, Any] = {
            "callback_query_id": callback_query_id,
            "show_alert": "true" if show_alert else "false",
        }
        if text:
            payload["text"] = text[:200]
        self.telegram_request("answerCallbackQuery", payload)

    def send_photo(self, chat_id: int, photo: bytes, caption: str, filename: str) -> None:
        self.telegram_upload(
            "sendPhoto",
            fields={
                "chat_id": chat_id,
                "caption": caption,
            },
            files={
                "photo": (filename, "image/png", photo),
            },
        )

    def telegram_request(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = TELEGRAM_API_BASE.format(token=self.token, method=method)
        return http_post_json(url, payload)

    def telegram_upload(
        self,
        method: str,
        fields: dict[str, Any],
        files: dict[str, tuple[str, str, bytes]],
    ) -> dict[str, Any]:
        url = TELEGRAM_API_BASE.format(token=self.token, method=method)
        return http_post_multipart_json(url, fields, files)


def parse_user_request(text: str) -> dict[str, Any]:
    clean = text.strip()
    lower = clean.lower()

    parts = clean.split()
    first = parts[0].split("@", 1)[0].lower() if parts else ""

    contract_address = extract_contract_address(clean)
    if contract_address and not first.startswith("/"):
        return {"kind": "token_lookup", "address": contract_address}

    if first in {"/ca", "/token"}:
        if len(parts) < 2:
            raise BotError("Format: /ca 0xcontract")
        contract_address = extract_contract_address(parts[1])
        if not contract_address:
            raise BotError("Contract address tidak valid.")
        return {"kind": "token_lookup", "address": contract_address}

    if first in {"/p", "/stats"}:
        if len(parts) < 2:
            raise BotError("Format: /p btc")
        return {"kind": "stats", "coin": parts[1]}

    if first in {"/mp", "/multi", "/prices"}:
        if len(parts) < 2:
            raise BotError("Format: /mp btc sol eth")
        coins = parts[1:]
        if len(coins) > 6:
            raise BotError("Maksimal 6 coin per request. Contoh: /mp btc sol eth")
        return {"kind": "multi_stats", "coins": coins}

    if lower.startswith(("/tv", "/chart")):
        parts = clean.split()
        if len(parts) < 2:
            raise BotError("Format: /tv btc atau /tv btc 15m")
        timeframe = normalize_timeframe(parts[2]) if len(parts) >= 3 else "1h"
        return {"kind": "chart", "coin": parts[1], "timeframe": timeframe}

    if lower.startswith(("/kline", "/candle", "/tf")):
        parts = clean.split()
        if len(parts) < 3:
            raise BotError("Format: /kline btc 15m")
        return {"kind": "kline", "coin": parts[1], "timeframe": normalize_timeframe(parts[2])}

    if lower.startswith("/price"):
        parts = clean.split()
        if len(parts) < 2:
            raise BotError("Format: /price btc, /price eth idr, atau /price btc 15m")
        coin = parts[1]
        if len(parts) >= 3 and is_timeframe(parts[2]):
            return {"kind": "chart", "coin": coin, "timeframe": normalize_timeframe(parts[2])}
        currency = normalize_currency(parts[2]) if len(parts) >= 3 else None
        return {"kind": "price", "coin": coin, "currency": currency}

    if lower.startswith("/convert"):
        parts = clean.replace(" to ", " ").replace(" ke ", " ").split()
        if len(parts) < 4:
            raise BotError("Format: /convert 0.5 btc usd")
        amount = parse_amount(parts[1])
        coin = parts[2]
        currency = normalize_currency(parts[3])
        return {"kind": "convert", "amount": amount, "coin": coin, "currency": currency}

    quick_default_convert = re.fullmatch(
        r"(?P<amount>\d+(?:[.,]\d+)?)\s+(?P<coin>[a-zA-Z0-9$._-]+)",
        lower,
    )
    if quick_default_convert:
        return {
            "kind": "convert_default",
            "amount": parse_amount(quick_default_convert.group("amount")),
            "coin": quick_default_convert.group("coin"),
        }

    quick_convert = re.fullmatch(
        r"(?P<amount>\d+(?:[.,]\d+)?)\s+(?P<coin>[a-zA-Z0-9$._-]+)\s+(?:to|ke)\s+(?P<currency>usd|usdt|idr)",
        lower,
    )
    if quick_convert:
        return {
            "kind": "convert",
            "amount": parse_amount(quick_convert.group("amount")),
            "coin": quick_convert.group("coin"),
            "currency": normalize_currency(quick_convert.group("currency")),
        }

    if len(parts) == 2 and is_timeframe(parts[1]):
        return {"kind": "chart", "coin": parts[0], "timeframe": normalize_timeframe(parts[1])}

    quick_price = re.fullmatch(r"(?P<coin>[a-zA-Z0-9$._-]+)(?:\s+(?P<currency>usd|usdt|idr))?", lower)
    if quick_price:
        currency = quick_price.group("currency")
        return {
            "kind": "price",
            "coin": quick_price.group("coin"),
            "currency": normalize_currency(currency) if currency else None,
        }

    raise BotError("Format belum dikenali.")


def parse_token_callback_data(data: str) -> dict[str, str]:
    parts = data.split("|")
    if len(parts) != 4 or parts[0] != TOKEN_CALLBACK_PREFIX:
        raise BotError("Tombol ini tidak valid.")

    action, chain_id, address = parts[1], parts[2], parts[3]
    if action not in {"d", "r"}:
        raise BotError("Action tombol tidak valid.")
    if chain_id not in SUPPORTED_TOKEN_CHAINS:
        raise BotError("Chain token tidak didukung.")

    return {
        "action": action,
        "chain_id": chain_id,
        "address": normalize_contract_address(address),
    }


def normalize_currency(currency: str | None) -> str:
    if not currency:
        raise BotError("Target mata uang belum diisi. Pilih USD, USDT, atau IDR.")

    normalized = currency.lower().strip()
    if normalized not in {"usd", "usdt", "idr"}:
        raise BotError("Target mata uang hanya mendukung USD, USDT, atau IDR.")
    return normalized


def normalize_timeframe(timeframe: str | None) -> str:
    if not timeframe:
        raise BotError("Timeframe belum diisi. Contoh: btc 15m")

    raw = timeframe.strip()
    if raw == "1M":
        return "1M"

    normalized = raw.lower()
    if normalized in TIMEFRAME_ALIASES:
        return TIMEFRAME_ALIASES[normalized]

    for interval in BINANCE_TIMEFRAMES:
        if interval != "1M" and normalized == interval:
            return interval

    raise BotError(f"Timeframe tidak didukung. Pilih: {', '.join(BINANCE_TIMEFRAMES)}")


def is_timeframe(value: str | None) -> bool:
    try:
        normalize_timeframe(value)
    except BotError:
        return False
    return True


def is_quick_convert_amount_coin(text: str) -> bool:
    return bool(re.fullmatch(r"\d+(?:[.,]\d+)?\s+[a-zA-Z0-9$._-]+", text.strip()))


def extract_contract_address(text: str) -> str | None:
    match = re.search(r"0x[a-fA-F0-9]{40}", text)
    return match.group(0) if match else None


def normalize_contract_address(address: str) -> str:
    match = re.fullmatch(r"0x[a-fA-F0-9]{40}", address.strip())
    if not match:
        raise BotError("Contract address tidak valid.")
    return address.strip()


def is_supported_token_pair(pair: dict[str, Any], address: str) -> bool:
    chain_id = str(pair.get("chainId") or "")
    if chain_id not in SUPPORTED_TOKEN_CHAINS:
        return False
    base_token = pair.get("baseToken") if isinstance(pair.get("baseToken"), dict) else {}
    return str(base_token.get("address") or "").lower() == address.lower()


def format_message_user(message: dict[str, Any]) -> str:
    user = message.get("from") if isinstance(message.get("from"), dict) else {}
    return format_user_label(user)


def format_callback_user(callback_query: dict[str, Any]) -> str:
    user = callback_query.get("from") if isinstance(callback_query.get("from"), dict) else {}
    return format_user_label(user)


def format_user_label(user: dict[str, Any]) -> str:
    username = user.get("username")
    if username:
        return f"@{username}"
    name = " ".join(part for part in (user.get("first_name"), user.get("last_name")) if part)
    if name:
        return name
    user_id = user.get("id")
    return str(user_id) if user_id is not None else "unknown"


def parse_amount(raw_amount: str) -> Decimal:
    try:
        amount = Decimal(raw_amount.replace(",", "."))
    except InvalidOperation as exc:
        raise BotError("Jumlah crypto tidak valid. Contoh: 0.5") from exc

    if amount <= 0:
        raise BotError("Jumlah crypto harus lebih dari 0.")
    return amount


def decimal_from_payload(payload: dict[str, Any], key: str) -> Decimal | None:
    value = payload.get(key)
    return decimal_from_any(value)


def decimal_from_any(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def decimal_from_path(payload: dict[str, Any], path: tuple[str, ...]) -> Decimal | None:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return decimal_from_any(current)


def int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def percent_decimal_from_ratio(value: Any) -> Decimal | None:
    decimal_value = decimal_from_any(value)
    if decimal_value is None:
        return None
    if abs(decimal_value) <= Decimal("1"):
        return decimal_value * Decimal("100")
    return decimal_value


def value_to_yes_no(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text == "1" or text == "true":
        return "Yes"
    if text == "0" or text == "false":
        return "No"
    return str(value)


def extract_websites(info: dict[str, Any]) -> list[TokenLink]:
    websites = info.get("websites")
    if not isinstance(websites, list):
        return []
    result: list[TokenLink] = []
    for website in websites:
        if isinstance(website, dict) and isinstance(website.get("url"), str):
            label = str(website.get("label") or "Web").strip() or "Web"
            result.append(TokenLink(label=label, url=website["url"]))
    return result


def extract_socials(info: dict[str, Any]) -> list[TokenLink]:
    socials = info.get("socials")
    if not isinstance(socials, list):
        return []
    links: list[TokenLink] = []
    for social in socials:
        if not isinstance(social, dict):
            continue
        platform = str(social.get("platform") or social.get("type") or "").lower()
        url = social.get("url")
        if not isinstance(url, str):
            continue
        if platform in {"twitter", "x"}:
            label = "X"
        elif platform in {"telegram", "tg"}:
            label = "TG"
        elif platform:
            label = platform.upper()
        else:
            label = "Social"
        links.append(TokenLink(label=label, url=url))
    return links


def format_market_stats(stats: MarketStats) -> str:
    return "\n".join(
        [
            f"Price: {format_usd_price(stats.price)}",
            f"⤷ ₿ {format_asset_amount(stats.btc_value)} | Ξ {format_asset_amount(stats.eth_value)}",
            f"⚖️ H/L: {format_optional_usd(stats.high_24h)} | {format_optional_usd(stats.low_24h)}",
            format_percent_line("1h", stats.change_1h, "🚀"),
            format_percent_line("24h", stats.change_24h, "🚀"),
            format_percent_line("7d", stats.change_7d, "🚀"),
            format_percent_line("30d", stats.change_30d, "🌕"),
            f"🏆 ATH: {format_optional_usd(stats.ath)} ({format_optional_percent(stats.ath_change_percent)})",
            f"📊 24h Vol: {format_optional_compact_usd(stats.volume_24h)}",
            f"💎 MCap: {format_optional_compact_usd(stats.market_cap)}",
        ]
    )


def format_percent_line(label: str, percent: Decimal | None, positive_icon: str) -> str:
    icon = "📉" if percent is not None and percent < 0 else positive_icon
    return f"{icon} {label}: {format_optional_percent(percent)}"


def format_multi_market_stats(stats_list: list[MarketStats]) -> str:
    lines = ["📊 Market Prices"]
    for index, stats in enumerate(stats_list):
        if index:
            lines.append("")
        lines.extend(
            [
                f"{stats.asset}: {format_usd_price(stats.price)}",
                (
                    f"1h: {format_optional_percent(stats.change_1h)} | "
                    f"24h: {format_optional_percent(stats.change_24h)} | "
                    f"7d: {format_optional_percent(stats.change_7d)}"
                ),
                (
                    f"Vol: {format_optional_compact_usd(stats.volume_24h)} | "
                    f"MCap: {format_optional_compact_usd(stats.market_cap)}"
                ),
            ]
        )
    return "\n".join(lines)


def build_token_text_reply(snapshot: TokenSnapshot, mark: TokenMark) -> TextReply:
    return TextReply(
        text=format_token_snapshot(snapshot, mark),
        parse_mode="HTML",
        reply_markup=build_token_reply_markup(snapshot),
    )


def build_token_reply_markup(snapshot: TokenSnapshot) -> dict[str, Any]:
    open_url = snapshot.pair_url or explorer_token_url(snapshot)
    buttons = [
        {"text": "🗑", "callback_data": token_callback_data("d", snapshot)},
        {"text": "↻", "callback_data": token_callback_data("r", snapshot)},
    ]
    if open_url:
        buttons.append({"text": "💎 ↗", "url": open_url})
    return {"inline_keyboard": [buttons]}


def token_callback_data(action: str, snapshot: TokenSnapshot) -> str:
    return f"{TOKEN_CALLBACK_PREFIX}|{action}|{snapshot.chain_id}|{snapshot.address.lower()}"


def format_token_snapshot(snapshot: TokenSnapshot, mark: TokenMark) -> str:
    market_cap = snapshot.market_cap or snapshot.fdv
    token_name = f"{snapshot.symbol} ({snapshot.name})"
    pair_meta = f"#{snapshot.chain_tag} | {snapshot.dex_id.upper()} | age {format_pair_age(snapshot.pair_created_at_ms)}"
    explorer_url = explorer_token_url(snapshot)
    address_line = f"├<code>{html_escape(snapshot.address)}</code>"
    if explorer_url:
        address_line += f' <a href="{html_escape(explorer_url, quote=True)}">scan</a>'

    lines = [
        f"🔎 <b>{html_escape(token_name)}</b>",
        address_line,
        f"└<code>{html_escape(pair_meta)}</code>",
        "",
        "📊 <b>Stats</b>",
        format_token_data_line("├", "USD", format_optional_usd(snapshot.price_usd)),
        format_token_data_line("├", "MC", format_optional_compact_usd(market_cap)),
        format_token_data_line("├", "Vol", format_optional_compact_usd(snapshot.volume_24h)),
        format_token_data_line("├", "LP", format_optional_compact_usd(snapshot.liquidity_usd)),
        format_token_data_line(
            "├",
            "1H",
            format_optional_percent(snapshot.change_h1),
            f" | {format_txns_html(snapshot.buys_h1, snapshot.sells_h1)}",
        ),
        format_token_data_line(
            "└",
            "24H",
            format_optional_percent(snapshot.change_h24),
            f" | {format_txns_html(snapshot.buys_h24, snapshot.sells_h24)}",
        ),
        "",
        f"🔗 <b>Socials</b>",
        f"└ {format_social_links_html(snapshot, explorer_url)}",
        "",
        "🔒 <b>Security</b>",
        *format_security_lines(snapshot.security),
        "",
        format_token_tools_links_html(snapshot),
        "",
        format_contract_mark_html(mark, market_cap),
    ]
    return "\n".join(lines)


def format_security_lines(security: TokenSecurity | None) -> list[str]:
    if security is None:
        return ["└ Security data unavailable"]
    return [
        format_token_data_line("├", "Tax", f"{format_security_percent(security.buy_tax)} buy / {format_security_percent(security.sell_tax)} sell"),
        format_token_data_line("├", "Honey", security.is_honeypot or "N/A"),
        format_token_data_line("├", "Source", security.is_open_source or "N/A"),
        format_token_data_line("├", "Top10", format_security_percent(security.top_10_holder_rate)),
        format_token_data_line("└", "Holders", f"{security.holder_count or 'N/A'} | LP {security.lp_holder_count or 'N/A'}"),
    ]


def format_contract_mark_html(mark: TokenMark, current_market_cap: Decimal | None) -> str:
    first_mcap = mark.first_market_cap
    elapsed = format_elapsed_seconds(int(time.time()) - mark.first_seen)
    icon = "🆕" if mark.is_new else "😈"
    if first_mcap and first_mcap > 0 and current_market_cap is not None:
        change = ((current_market_cap - first_mcap) / first_mcap) * Decimal("100")
        change_text = format_mark_percent(change)
    else:
        change_text = "N/A"
    return (
        f"{icon} <b>{html_escape(mark.first_user)}</b> @ "
        f"<b>{html_escape(format_optional_compact_usd(first_mcap))}</b> "
        f"<b>[{html_escape(change_text)}]</b> ({html_escape(elapsed)})"
    )


def format_token_data_line(prefix: str, label: str, value: str, extra_html: str = "") -> str:
    return f"{prefix}<code>{html_escape(label + ':'):<8}</code> <b>{html_escape(value)}</b>{extra_html}"


def format_txns_html(buys: int | None, sells: int | None) -> str:
    return f"🟢 <b>{buys or 0}</b> 🔴 <b>{sells or 0}</b>"


def format_social_links_html(snapshot: TokenSnapshot, explorer_url: str | None) -> str:
    links: list[TokenLink] = []
    links.extend(snapshot.socials)
    links.extend(TokenLink(label=normalize_website_label(link.label), url=link.url) for link in snapshot.websites)
    if explorer_url:
        links.append(TokenLink(label="Scan", url=explorer_url))

    unique_links = dedupe_token_links(links)
    if not unique_links:
        return "N/A"
    return " · ".join(format_html_link(link.label, link.url) for link in unique_links)


def format_token_tools_links_html(snapshot: TokenSnapshot) -> str:
    links = [
        TokenLink(label="gmgn", url=gmgn_token_url(snapshot)),
        TokenLink(label="okx", url=okx_web3_token_url(snapshot)),
    ]
    if snapshot.pair_url:
        links.append(TokenLink(label="dex", url=snapshot.pair_url))
    return " · ".join(format_html_link(link.label, link.url) for link in links)


def dedupe_token_links(links: list[TokenLink]) -> list[TokenLink]:
    seen: set[tuple[str, str]] = set()
    result: list[TokenLink] = []
    for link in links:
        key = (link.label.lower(), link.url)
        if key in seen:
            continue
        seen.add(key)
        result.append(link)
    return result


def normalize_website_label(label: str) -> str:
    normalized = label.strip()
    if not normalized or normalized.lower() in {"website", "site", "homepage"}:
        return "Web"
    return normalized[:12]


def format_html_link(label: str, url: str) -> str:
    return f'<a href="{html_escape(url, quote=True)}">{html_escape(label)}</a>'


def gmgn_token_url(snapshot: TokenSnapshot) -> str:
    config = SUPPORTED_TOKEN_CHAINS.get(snapshot.chain_id, {})
    chain = str(config.get("gmgn_chain") or snapshot.chain_id)
    return f"https://gmgn.ai/{chain}/token/{GMGN_REFERRAL_ID}_{snapshot.address}"


def okx_web3_token_url(snapshot: TokenSnapshot) -> str:
    config = SUPPORTED_TOKEN_CHAINS.get(snapshot.chain_id, {})
    chain = str(config.get("okx_chain") or snapshot.chain_id)
    return f"https://web3.okx.com/explorer/{chain}/token/{snapshot.address}"


def explorer_token_url(snapshot: TokenSnapshot) -> str | None:
    if snapshot.chain_id == "ethereum":
        return f"https://etherscan.io/token/{snapshot.address}"
    if snapshot.chain_id == "base":
        return f"https://basescan.org/token/{snapshot.address}"
    if snapshot.chain_id == "bsc":
        return f"https://bscscan.com/token/{snapshot.address}"
    return None


def html_escape(value: Any, quote: bool = False) -> str:
    return html.escape(str(value), quote=quote)


def short_address(address: str) -> str:
    cleaned = address.strip()
    if len(cleaned) <= 14:
        return cleaned
    return f"{cleaned[:6]}...{cleaned[-4:]}"


def format_pair_age(pair_created_at_ms: int | None) -> str:
    if not pair_created_at_ms:
        return "N/A"
    elapsed = int(time.time()) - int(pair_created_at_ms / 1000)
    return format_elapsed_seconds(elapsed)


def format_elapsed_seconds(seconds: int) -> str:
    if seconds < 0:
        seconds = 0
    minute = 60
    hour = minute * 60
    day = hour * 24
    week = day * 7
    month = day * 30
    year = day * 365
    if seconds >= year:
        return f"{seconds // year}y"
    if seconds >= month:
        return f"{seconds // month}mo"
    if seconds >= week:
        return f"{seconds // week}w"
    if seconds >= day:
        return f"{seconds // day}d"
    if seconds >= hour:
        return f"{seconds // hour}h"
    if seconds >= minute:
        return f"{seconds // minute}m"
    return f"{seconds}s"


def format_socials(snapshot: TokenSnapshot) -> str:
    labels = [link.label for link in snapshot.socials]
    labels.extend(normalize_website_label(link.label) for link in snapshot.websites)
    if not labels:
        return "N/A"
    return " · ".join(sorted(set(labels)))


def format_txns(buys: int | None, sells: int | None) -> str:
    return f"🟢 {buys or 0} 🔴 {sells or 0}"


def format_security_percent(value: Decimal | None) -> str:
    if value is None:
        return "N/A"
    return f"{format_decimal(value.quantize(Decimal('0.01')))}%"


def format_mark_percent(value: Decimal) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{format_decimal(value.quantize(Decimal('0.01')))}%"


def format_gas_estimate(estimate: GasEstimate) -> str:
    lines = [
        "⛽ Ethereum Gas",
        f"🐢 Safe: {format_gwei(estimate.safe_gwei)} gwei",
        f"⚖️ Standard: {format_gwei(estimate.standard_gwei)} gwei",
        f"🚀 Fast: {format_gwei(estimate.fast_gwei)} gwei",
    ]
    if estimate.base_fee_gwei is not None:
        lines.append(f"Base fee: {format_gwei(estimate.base_fee_gwei)} gwei")
    if estimate.gas_used_ratio is not None:
        lines.append(f"Network use: {format_decimal((estimate.gas_used_ratio * Decimal('100')).quantize(Decimal('0.01')))}%")
    if estimate.last_block:
        lines.append(f"Block: {estimate.last_block}")
    lines.append(f"Source: {estimate.source}")
    return "\n".join(lines)


def parse_fee_history(payload: Any, source: str) -> GasEstimate:
    if not isinstance(payload, dict):
        raise BotError(f"{source} response tidak valid.")
    if "error" in payload:
        raise BotError(f"{source}: {payload['error']}")

    result = payload.get("result")
    if not isinstance(result, dict):
        raise BotError(f"{source} tidak mengembalikan fee history.")

    base_fees = result.get("baseFeePerGas")
    rewards = result.get("reward")
    if not isinstance(base_fees, list) or not base_fees:
        raise BotError(f"{source} tidak mengembalikan base fee.")
    if not isinstance(rewards, list) or not rewards:
        raise BotError(f"{source} tidak mengembalikan priority fee.")

    base_fee_gwei = wei_hex_to_gwei(str(base_fees[-1]))
    priority_columns = collect_priority_fee_columns(rewards)
    if len(priority_columns) < 3:
        raise BotError(f"{source} priority fee percentile tidak lengkap.")

    safe_priority = average_decimal(priority_columns[0])
    standard_priority = average_decimal(priority_columns[1])
    fast_priority = average_decimal(priority_columns[2])
    oldest_block = int(str(result.get("oldestBlock", "0x0")), 16)
    last_block = oldest_block + len(rewards) - 1

    return GasEstimate(
        safe_gwei=base_fee_gwei + safe_priority,
        standard_gwei=base_fee_gwei + standard_priority,
        fast_gwei=base_fee_gwei + fast_priority,
        base_fee_gwei=base_fee_gwei,
        last_block=str(last_block) if last_block > 0 else None,
        gas_used_ratio=parse_latest_gas_used_ratio(result.get("gasUsedRatio")),
        source=f"Ethereum RPC eth_feeHistory ({source})",
    )


def collect_priority_fee_columns(rewards: list[Any]) -> list[list[Decimal]]:
    columns: list[list[Decimal]] = []
    for row in rewards:
        if not isinstance(row, list):
            continue
        for index, value in enumerate(row):
            while len(columns) <= index:
                columns.append([])
            columns[index].append(wei_hex_to_gwei(str(value)))
    return columns


def average_decimal(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    return sum(values, Decimal("0")) / Decimal(len(values))


def wei_hex_to_gwei(value: str) -> Decimal:
    return Decimal(int(value, 16)) / WEI_PER_GWEI


def parse_latest_gas_used_ratio(value: Any) -> Decimal | None:
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",") if part.strip()]
        if not parts:
            return None
        value = parts[-1]
    elif isinstance(value, list):
        if not value:
            return None
        value = value[-1]

    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def reply_timeframes() -> str:
    return "Timeframe Binance yang tersedia:\n" + ", ".join(BINANCE_TIMEFRAMES)


def strip_bot_mention(text: str) -> str:
    first_word, *rest = text.split(maxsplit=1)
    if "@" in first_word:
        first_word = first_word.split("@", 1)[0]
    return " ".join([first_word, *rest]).strip()


def generic_get_json(url: str, params: dict[str, Any] | None = None) -> Any:
    query = urllib.parse.urlencode(params or {})
    full_url = f"{url}?{query}" if query else url
    request = urllib.request.Request(
        full_url,
        headers={
            "Accept": "application/json",
            "User-Agent": "crypto-telegram-converter/2.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        message = parse_error_message(body) or str(exc)
        raise BotError(f"HTTP error ({exc.code}): {message}") from exc
    except urllib.error.URLError as exc:
        raise BotError(f"{url} gagal diakses: {exc.reason}") from exc


def generic_post_json(url: str, payload: dict[str, Any]) -> Any:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "crypto-telegram-converter/2.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        message = parse_error_message(body) or str(exc)
        raise BotError(f"{url} HTTP error ({exc.code}): {message}") from exc
    except urllib.error.URLError as exc:
        raise BotError(f"{url} gagal diakses: {exc.reason}") from exc


def http_post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"User-Agent": "crypto-telegram-converter/2.0"},
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS + POLL_TIMEOUT_SECONDS) as response:
        data = json.loads(response.read().decode("utf-8"))

    if not data.get("ok"):
        description = data.get("description", "Telegram API error")
        raise BotError(description)
    return data


def http_post_multipart_json(
    url: str,
    fields: dict[str, Any],
    files: dict[str, tuple[str, str, bytes]],
) -> dict[str, Any]:
    boundary = f"----CryptoWhaleBoundary{secrets.token_hex(16)}"
    body_parts: list[bytes] = []

    for name, value in fields.items():
        body_parts.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
                str(value).encode("utf-8"),
                b"\r\n",
            ]
        )

    for name, (filename, content_type, content) in files.items():
        body_parts.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                (
                    f'Content-Disposition: form-data; name="{name}"; '
                    f'filename="{filename}"\r\n'
                ).encode("utf-8"),
                f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"),
                content,
                b"\r\n",
            ]
        )

    body_parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(body_parts)
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
            "User-Agent": "crypto-telegram-converter/2.0",
        },
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS + POLL_TIMEOUT_SECONDS) as response:
        data = json.loads(response.read().decode("utf-8"))

    if not data.get("ok"):
        description = data.get("description", "Telegram API error")
        raise BotError(description)
    return data


def parse_error_message(body: str) -> str | None:
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return body.strip() or None

    if isinstance(data, dict):
        code = data.get("code")
        message = data.get("msg") or data.get("message")
        if code is not None and message:
            return f"{code}: {message}"
        if message:
            return str(message)
    return body.strip() or None


def parse_api_bases(raw_value: str | None) -> tuple[str, ...]:
    return parse_url_list(raw_value, BINANCE_API_BASES)


def parse_url_list(raw_value: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if not raw_value:
        return default

    urls = tuple(value.strip() for value in raw_value.split(",") if value.strip())
    return urls or default


def load_env_file(path: str = ".env") -> None:
    if not os.path.isabs(path):
        path = os.path.join(BASE_DIR, path)

    if not os.path.exists(path):
        return

    with open(path, "r", encoding="utf-8") as env_file:
        for line in env_file:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_offset(path: str = ".telegram_offset") -> int | None:
    if not os.path.isabs(path):
        path = os.path.join(BASE_DIR, path)

    if not os.path.exists(path):
        return None

    try:
        with open(path, "r", encoding="utf-8") as offset_file:
            return int(offset_file.read().strip())
    except (OSError, ValueError):
        return None


def save_offset(offset: int, path: str = ".telegram_offset") -> None:
    if not os.path.isabs(path):
        path = os.path.join(BASE_DIR, path)

    with open(path, "w", encoding="utf-8") as offset_file:
        offset_file.write(str(offset))


def format_money(value: Decimal, currency: str) -> str:
    if currency == "idr":
        rounded = value.quantize(Decimal("1"))
        return f"Rp {format_int_with_separator(int(rounded), '.')}"

    return format_quote_money(value, currency.upper())


def format_usd_price(value: Decimal) -> str:
    if value >= Decimal("1000"):
        return f"${value.quantize(Decimal('1')):,.0f}"
    if value >= Decimal("1"):
        return f"${value.quantize(Decimal('0.01')):,.2f}"
    return f"${format_small_decimal(value)}"


def format_optional_usd(value: Decimal | None) -> str:
    return format_usd_price(value) if value is not None else "N/A"


def format_optional_percent(value: Decimal | None) -> str:
    if value is None:
        return "N/A"
    return f"{format_decimal(value.quantize(Decimal('0.01')))}%"


def format_optional_compact_usd(value: Decimal | None) -> str:
    return format_compact_usd(value) if value is not None else "N/A"


def format_compact_usd(value: Decimal) -> str:
    units = (
        (Decimal("1000000000000"), "T"),
        (Decimal("1000000000"), "B"),
        (Decimal("1000000"), "M"),
        (Decimal("1000"), "K"),
    )
    abs_value = abs(value)
    for divisor, suffix in units:
        if abs_value >= divisor:
            return f"${format_decimal((value / divisor).quantize(Decimal('0.01')))}{suffix}"
    return format_usd_price(value)


def format_asset_amount(value: Decimal) -> str:
    if value >= Decimal("1"):
        return f"{value.quantize(Decimal('0.01')):,.2f}"
    if value >= Decimal("0.0001"):
        return format_decimal(value.quantize(Decimal("0.000001")))
    return format_small_decimal(value)


def format_gwei(value: Decimal) -> str:
    if value >= Decimal("100"):
        return format_decimal(value.quantize(Decimal("0.1")))
    if value >= Decimal("1"):
        return format_decimal(value.quantize(Decimal("0.01")))
    return format_decimal(value.quantize(Decimal("0.001")))


def format_quote_money(value: Decimal, quote_asset: str) -> str:
    if value >= Decimal("1"):
        return f"{quote_asset} {value.quantize(Decimal('0.01')):,.2f}"
    return f"{quote_asset} {format_small_decimal(value)}"


def format_chart_price(value: Decimal) -> str:
    if value >= Decimal("1"):
        return f"{value.quantize(Decimal('0.01')):,.2f}"
    return format_small_decimal(value)


def format_decimal(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    if "." in text:
        return text.rstrip("0").rstrip(".")
    return text


def format_small_decimal(value: Decimal) -> str:
    text = f"{value:.10f}".rstrip("0").rstrip(".")
    return text if text != "0" else "0.0000000000"


def format_int_with_separator(value: int, separator: str) -> str:
    return f"{value:,}".replace(",", separator)


def format_datetime(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def format_ms_timestamp(timestamp_ms: int) -> str:
    dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
    return format_datetime(dt)


def format_chart_time(timestamp_ms: int, interval: str) -> str:
    dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
    if interval.endswith("m") or interval.endswith("h"):
        return dt.strftime("%d %b\n%H:%M")
    if interval.endswith("d") or interval.endswith("w"):
        return dt.strftime("%d %b")
    return dt.strftime("%b %Y")


def build_chart_ticks(length: int, target_count: int) -> list[int]:
    if length <= 1:
        return [0]
    count = max(2, min(target_count, length))
    return sorted({round(index * (length - 1) / (count - 1)) for index in range(count)})


def main() -> int:
    load_env_file()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("TELEGRAM_BOT_TOKEN belum diisi. Buat .env dari .env.example dulu.", file=sys.stderr)
        return 1

    api_bases = parse_api_bases(os.environ.get("BINANCE_API_BASES") or os.environ.get("BINANCE_API_BASE"))
    coingecko_api_base = os.environ.get("COINGECKO_API_BASE", COINGECKO_API_BASE)
    eth_rpc_urls = parse_url_list(os.environ.get("ETH_RPC_URLS"), ETH_RPC_URLS)
    bot = TelegramBot(
        token=token,
        price_client=BinanceMarketClient(api_bases=api_bases),
        stats_client=MarketStatsClient(api_base=coingecko_api_base),
        gas_client=GasClient(
            etherscan_api_key=os.environ.get("ETHERSCAN_API_KEY"),
            rpc_urls=eth_rpc_urls,
        ),
        token_client=TokenLookupClient(),
        mark_store=TokenMarkStore(),
    )
    bot.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
