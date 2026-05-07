#!/usr/bin/env python3
"""
Telegram bot untuk cek harga crypto dan candle timeframe dari Binance.

Bot ini sengaja memakai Python standard library saja agar mudah dijalankan:
- Telegram Bot API via long polling
- Binance Spot public API untuk harga dan candlestick
"""

from __future__ import annotations

import json
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
CHART_CANDLE_LIMIT = 134

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
/convert 0.5 btc usd
/convert 250 doge idr
/tv eth
/tv eth 15m
/kline btc 15m
/timeframes

Format cepat juga bisa:
btc
eth idr
btc 15m
eth 30m
0.1 btc to idr

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
    ) -> None:
        self.token = token
        self.price_client = price_client
        self.stats_client = stats_client
        self.offset = load_offset()

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
            "allowed_updates": json.dumps(["message"]),
        }
        response = self.telegram_request("getUpdates", payload)
        return response.get("result", [])

    def handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        text = (message.get("text") or "").strip()

        if chat_id is None or not text:
            return

        try:
            reply = self.build_reply(text)
        except BotError as exc:
            reply = f"{exc}\n\nKetik /help untuk contoh command."

        if isinstance(reply, PhotoReply):
            self.send_photo(chat_id, reply.photo, reply.caption, reply.filename)
        else:
            self.send_message(chat_id, reply)

    def build_reply(self, text: str) -> str | PhotoReply:
        command = strip_bot_mention(text)
        lower = command.lower().strip()

        if lower in {"/start", "start", "/help", "help"}:
            return HELP_TEXT
        if lower in {"/timeframes", "timeframes", "/tf", "tf"}:
            return reply_timeframes()

        request = parse_user_request(command)
        if request["kind"] == "price":
            return self.reply_price(request["coin"], request["currency"])
        if request["kind"] == "stats":
            return self.reply_market_stats(request["coin"])
        if request["kind"] == "convert":
            return self.reply_convert(request["amount"], request["coin"], request["currency"])
        if request["kind"] == "kline":
            return self.reply_kline(request["coin"], request["timeframe"])
        if request["kind"] == "chart":
            return self.reply_chart(request["coin"], request["timeframe"])

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

    def send_message(self, chat_id: int, text: str) -> None:
        self.telegram_request(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True,
            },
        )

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

    if first in {"/p", "/stats"}:
        if len(parts) < 2:
            raise BotError("Format: /p btc")
        return {"kind": "stats", "coin": parts[1]}

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
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


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


def reply_timeframes() -> str:
    return "Timeframe Binance yang tersedia:\n" + ", ".join(BINANCE_TIMEFRAMES)


def strip_bot_mention(text: str) -> str:
    first_word, *rest = text.split(maxsplit=1)
    if "@" in first_word:
        first_word = first_word.split("@", 1)[0]
    return " ".join([first_word, *rest]).strip()


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
    if not raw_value:
        return BINANCE_API_BASES

    bases = tuple(base.strip() for base in raw_value.split(",") if base.strip())
    return bases or BINANCE_API_BASES


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
    bot = TelegramBot(
        token=token,
        price_client=BinanceMarketClient(api_bases=api_bases),
        stats_client=MarketStatsClient(api_base=coingecko_api_base),
    )
    bot.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
