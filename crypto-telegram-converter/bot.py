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
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any


TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}/{method}"
BINANCE_API_BASE = "https://api.binance.com"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_VS_CURRENCIES = ("usd", "idr")
REQUEST_TIMEOUT_SECONDS = 20
POLL_TIMEOUT_SECONDS = 35
PRICE_CACHE_SECONDS = 5
KLINE_CACHE_SECONDS = 5

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

HELP_TEXT = f"""Halo! Kirim command seperti ini:

/price btc
/price eth idr
/convert 0.5 btc usd
/convert 250 doge idr
/kline btc 15m
/timeframes

Format cepat juga bisa:
btc
eth idr
btc 15m
eth 30m
0.1 btc to idr

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


class BinanceMarketClient:
    def __init__(self, api_base: str = BINANCE_API_BASE) -> None:
        self.api_base = api_base.rstrip("/")
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

    def binance_get_json(self, path: str, params: dict[str, Any]) -> Any:
        query = urllib.parse.urlencode(params)
        url = f"{self.api_base}{path}?{query}"
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
            raise BotError(f"Binance API error ({exc.code}): {message}") from exc


class TelegramBot:
    def __init__(self, token: str, price_client: BinanceMarketClient) -> None:
        self.token = token
        self.price_client = price_client
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

        self.send_message(chat_id, reply)

    def build_reply(self, text: str) -> str:
        command = strip_bot_mention(text)
        lower = command.lower().strip()

        if lower in {"/start", "start", "/help", "help"}:
            return HELP_TEXT
        if lower in {"/timeframes", "timeframes", "/tf", "tf"}:
            return reply_timeframes()

        request = parse_user_request(command)
        if request["kind"] == "price":
            return self.reply_price(request["coin"], request["currency"])
        if request["kind"] == "convert":
            return self.reply_convert(request["amount"], request["coin"], request["currency"])
        if request["kind"] == "kline":
            return self.reply_kline(request["coin"], request["timeframe"])

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

    def send_message(self, chat_id: int, text: str) -> None:
        self.telegram_request(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True,
            },
        )

    def telegram_request(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = TELEGRAM_API_BASE.format(token=self.token, method=method)
        return http_post_json(url, payload)


def parse_user_request(text: str) -> dict[str, Any]:
    clean = text.strip()
    lower = clean.lower()

    if lower.startswith(("/kline", "/candle", "/chart", "/tf")):
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
            return {"kind": "kline", "coin": coin, "timeframe": normalize_timeframe(parts[2])}
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

    parts = clean.split()
    if len(parts) == 2 and is_timeframe(parts[1]):
        return {"kind": "kline", "coin": parts[0], "timeframe": normalize_timeframe(parts[1])}

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


def format_quote_money(value: Decimal, quote_asset: str) -> str:
    if value >= Decimal("1"):
        return f"{quote_asset} {value.quantize(Decimal('0.01')):,.2f}"
    return f"{quote_asset} {format_small_decimal(value)}"


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


def main() -> int:
    load_env_file()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("TELEGRAM_BOT_TOKEN belum diisi. Buat .env dari .env.example dulu.", file=sys.stderr)
        return 1

    api_base = os.environ.get("BINANCE_API_BASE", BINANCE_API_BASE)
    bot = TelegramBot(token=token, price_client=BinanceMarketClient(api_base=api_base))
    bot.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
