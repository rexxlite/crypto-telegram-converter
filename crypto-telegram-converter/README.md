# Crypto Telegram Converter Bot

Bot Telegram sederhana untuk cek dan konversi harga crypto ke USD atau IDR secara real-time memakai CoinGecko public API.

## Fitur

- `/price btc` menampilkan harga BTC ke USD dan IDR.
- `/price eth idr` menampilkan harga ETH ke IDR.
- `/convert 0.5 btc usd` mengonversi jumlah coin ke USD.
- `/convert 250 doge idr` mengonversi jumlah coin ke IDR.
- Format cepat: `btc`, `eth idr`, `0.1 btc to idr`.

## Cara Menjalankan

1. Buat bot Telegram lewat [BotFather](https://t.me/BotFather), lalu salin token bot.
2. Salin file `.env.example` menjadi `.env`.
3. Isi token:

```env
TELEGRAM_BOT_TOKEN=token_dari_botfather
```

4. Jalankan bot:

```bash
python bot.py
```

5. Buka chat bot Telegram kamu, lalu kirim `/start`.

## Catatan

- Bot ini memakai long polling, jadi cocok untuk dijalankan di laptop/VPS tanpa setup webhook.
- Harga berasal dari CoinGecko dan bisa berubah cepat. Public API biasanya punya rate limit, jadi hindari spam request terlalu banyak.
- Coin yang sudah diberi alias umum: BTC, ETH, BNB, SOL, XRP, ADA, DOGE, DOT, TRX, LTC, BCH, LINK, AVAX, TON, SHIB, PEPE, USDT, USDC.
- Untuk coin lain, coba kirim CoinGecko ID-nya, misalnya `/price bitcoin` atau `/price ethereum`.
