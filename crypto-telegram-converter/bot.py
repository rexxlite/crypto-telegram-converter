#!/usr/bin/env python3
"""
Telegram bot untuk konversi harga crypto ke USD/IDR.

Bot ini sengaja memakai Python standard library saja agar mudah dijalankan:
- Telegram Bot API via long polling
- CoinGecko public API untuk harga crypto
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
COINGECKO_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_VS_CURRENCIES = ("usd", "idr")
REQUEST_TIMEOUT_SECONDS = 20
POLL_TIMEOUT_SECONDS = 35
PRICE_CACHE_SECONDS = 20

COMMON_COINS = {
    "btc": "bitcoin",
    "bitcoin": "bitcoin",
    "xbt": "bitcoin",
    "eth": "ethereum",
    "ethereum": "ethereum",
    "bnb": "binancecoin",
    "sol": "solana",
    "solana": "solana",
    "xrp": "ripple",
    "ripple": "ripple",
    "ada": "cardano",
    "cardano": "cardano",
    "doge": "dogecoin",
    "dogecoin": "dogecoin",
    "dot": "polkadot",
    "polkadot": "polkadot",
    "trx": "tron",
    "tron": "tron",
    "ltc": "litecoin",
    "litecoin": "litecoin",
    "bch": "bitcoin-cash",
    "link": "chainlink",
    "chainlink": "chainlink",
    "avax": "avalanche-2",
    "avalanche": "avalanche-2",
    "ton": "the-open-network",
    "toncoin": "the-open-network",
    "shib": "shiba-inu",
    "shiba": "shiba-inu",
    "pepe": "pepe",
    "usdt": "tether",
    "tether": "tether",
    "usdc": "usd-coin",
    "usd-coin": "usd-coin",
}

SYMBOL_BY_ID = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "binancecoin": "BNB",
    "solana": "SOL",
    "ripple": "XRP",
    "cardano": "ADA",
    "dogecoin": "DOGE",
    "polkadot": "DOT",
    "tron": "TRX",
    "litecoin": "LTC",
    "bitcoin-cash": "BCH",
    "chainlink": "LINK",
    "avalanche-2": "AVAX",
    "the-open-network": "TON",
    "shiba-inu": "SHIB",
    "pepe": "PEPE",
    "tether": "USDT",
    "usd-coin": "USDC",
}

HELP_TEXT = """Halo! Kirim command seperti ini:

/price btc
/price eth idr
/convert 0.5 btc usd
/convert 250 doge idr

Format cepat juga bisa:
btc
eth idr
0.1 btc to idr

Target mata uang yang didukung di bot ini: USD dan IDR.
"""


class BotError(Exception):
    """Error yang aman ditampilkan ke user Telegram."""


@dataclass
class PriceResult:
    coin_id: str
    symbol: str
    prices: dict[str, Decimal]
    last_updated_at: int | None = None


class CryptoPriceClient:
    def __init__(self) -> None:
        self._cache: dict[tuple[str, tuple[str, ...]], tuple[float, PriceResult]] = {}

    def resolve_coin_id(self, coin_text: str) -> str:
        normalized = coin_text.strip().lower()
        normalized = normalized.replace("$", "")
        if not normalized:
            raise BotError("Nama coin belum diisi. Contoh: /price btc")

        return COMMON_COINS.get(normalized, normalized)

    def get_prices(self, coin_text: str, vs_currencies: tuple[str, ...]) -> PriceResult:
        coin_id = self.resolve_coin_id(coin_text)
        vs = tuple(sorted({currency.lower() for currency in vs_currencies}))

        cache_key = (coin_id, vs)
        cached = self._cache.get(cache_key)
        now = time.time()
        if cached and now - cached[0] <= PRICE_CACHE_SECONDS:
            return cached[1]

        params = urllib.parse.urlencode(
            {
                "ids": coin_id,
                "vs_currencies": ",".join(vs),
                "include_last_updated_at": "true",
            }
        )
        data = http_get_json(f"{COINGECKO_PRICE_URL}?{params}")

        if coin_id not in data:
            raise BotError(
                f"Coin '{coin_text}' tidak ditemukan. Coba pakai simbol umum seperti btc, eth, sol, xrp."
            )

        coin_payload = data[coin_id]
        prices: dict[str, Decimal] = {}
        for currency in vs:
            value = coin_payload.get(currency)
            if value is not None:
                prices[currency] = Decimal(str(value))

        if not prices:
            raise BotError("Harga tidak tersedia untuk target mata uang itu.")

        result = PriceResult(
            coin_id=coin_id,
            symbol=SYMBOL_BY_ID.get(coin_id, coin_id.upper()),
            prices=prices,
            last_updated_at=coin_payload.get("last_updated_at"),
        )
        self._cache[cache_key] = (now, result)
        return result


class TelegramBot:
    def __init__(self, token: str, price_client: CryptoPriceClient) -> None:
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

        request = parse_user_request(command)
        if request["kind"] == "price":
            return self.reply_price(request["coin"], request["currency"])
        if request["kind"] == "convert":
            return self.reply_convert(request["amount"], request["coin"], request["currency"])

        raise BotError("Command belum dikenali.")

    def reply_price(self, coin: str, currency: str | None) -> str:
        targets = (currency.lower(),) if currency else DEFAULT_VS_CURRENCIES
        result = self.price_client.get_prices(coin, targets)

        lines = [f"Harga {result.symbol} sekarang:"]
        for target in targets:
            price = result.prices.get(target)
            if price is None:
                continue
            lines.append(f"- {target.upper()}: {format_money(price, target)}")

        if result.last_updated_at:
            lines.append(f"\nUpdate: {format_timestamp(result.last_updated_at)}")
        lines.append("Sumber: CoinGecko")
        return "\n".join(lines)

    def reply_convert(self, amount: Decimal, coin: str, currency: str) -> str:
        target = currency.lower()
        result = self.price_client.get_prices(coin, (target,))
        price = result.prices[target]
        total = amount * price

        lines = [
            f"{format_decimal(amount)} {result.symbol} = {format_money(total, target)}",
            f"Harga 1 {result.symbol}: {format_money(price, target)}",
        ]
        if result.last_updated_at:
            lines.append(f"Update: {format_timestamp(result.last_updated_at)}")
        lines.append("Sumber: CoinGecko")
        return "\n".join(lines)

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

    if lower.startswith("/price"):
        parts = clean.split()
        if len(parts) < 2:
            raise BotError("Format: /price btc atau /price eth idr")
        coin = parts[1]
        currency = normalize_currency(parts[2]) if len(parts) >= 3 else None
        return {"kind": "price", "coin": coin, "currency": currency}

    if lower.startswith("/convert"):
        parts = clean.replace(" to ", " ").split()
        if len(parts) < 4:
            raise BotError("Format: /convert 0.5 btc usd")
        amount = parse_amount(parts[1])
        coin = parts[2]
        currency = normalize_currency(parts[3])
        return {"kind": "convert", "amount": amount, "coin": coin, "currency": currency}

    quick_convert = re.fullmatch(
        r"(?P<amount>\d+(?:[.,]\d+)?)\s+(?P<coin>[a-zA-Z0-9$._-]+)\s+(?:to|ke)\s+(?P<currency>usd|idr)",
        lower,
    )
    if quick_convert:
        return {
            "kind": "convert",
            "amount": parse_amount(quick_convert.group("amount")),
            "coin": quick_convert.group("coin"),
            "currency": normalize_currency(quick_convert.group("currency")),
        }

    quick_price = re.fullmatch(r"(?P<coin>[a-zA-Z0-9$._-]+)(?:\s+(?P<currency>usd|idr))?", lower)
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
        raise BotError("Target mata uang belum diisi. Pilih USD atau IDR.")

    normalized = currency.lower().strip()
    if normalized not in {"usd", "idr"}:
        raise BotError("Target mata uang hanya mendukung USD atau IDR.")
    return normalized


def parse_amount(raw_amount: str) -> Decimal:
    try:
        amount = Decimal(raw_amount.replace(",", "."))
    except InvalidOperation as exc:
        raise BotError("Jumlah crypto tidak valid. Contoh: 0.5") from exc

    if amount <= 0:
        raise BotError("Jumlah crypto harus lebih dari 0.")
    return amount


def strip_bot_mention(text: str) -> str:
    first_word, *rest = text.split(maxsplit=1)
    if "@" in first_word:
        first_word = first_word.split("@", 1)[0]
    return " ".join([first_word, *rest]).strip()


def http_get_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "crypto-telegram-converter/1.0"},
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def http_post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"User-Agent": "crypto-telegram-converter/1.0"},
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS + POLL_TIMEOUT_SECONDS) as response:
        data = json.loads(response.read().decode("utf-8"))

    if not data.get("ok"):
        description = data.get("description", "Telegram API error")
        raise BotError(description)
    return data


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

    if value >= Decimal("1"):
        return f"${value.quantize(Decimal('0.01')):,.2f}"

    return f"${format_small_decimal(value)}"


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


def format_timestamp(timestamp: int) -> str:
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def main() -> int:
    load_env_file()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("TELEGRAM_BOT_TOKEN belum diisi. Buat .env dari .env.example dulu.", file=sys.stderr)
        return 1

    bot = TelegramBot(token=token, price_client=CryptoPriceClient())
    bot.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
