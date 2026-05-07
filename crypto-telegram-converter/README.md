# Crypto Telegram Converter Bot

Bot Telegram sederhana untuk cek harga crypto, konversi, dan candle timeframe secara real-time memakai Binance Spot public API.

## Fitur

- `/price btc` menampilkan harga BTC ke USD dan IDR.
- `/price eth idr` menampilkan harga ETH ke IDR.
- `/convert 0.5 btc usd` mengonversi jumlah coin ke USD.
- `/convert 250 doge idr` mengonversi jumlah coin ke IDR.
- `/kline btc 15m` menampilkan candle BTCUSDT timeframe 15 menit.
- `/timeframes` menampilkan daftar timeframe Binance yang didukung.
- Format cepat: `btc`, `eth idr`, `btc 15m`, `eth 30m`, `0.1 btc to idr`.

## Cara Menjalankan

1. Buat bot Telegram lewat [BotFather](https://t.me/BotFather), lalu salin token bot.
2. Salin file `.env.example` menjadi `.env`.
3. Isi token:

```env
TELEGRAM_BOT_TOKEN=token_dari_botfather
```

4. Jalankan bot:

```bash
python3 bot.py
```

5. Buka chat bot Telegram kamu, lalu kirim `/start`.

## Catatan

- Bot ini memakai long polling, jadi cocok untuk dijalankan di laptop/VPS tanpa setup webhook.
- Harga dan candle berasal dari Binance Spot public API dan bisa berubah cepat. Public API punya rate limit, jadi hindari spam request terlalu banyak.
- Harga USD memakai pair USDT Binance, misalnya BTCUSDT.
- Timeframe Binance yang didukung: `1s`, `1m`, `3m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `6h`, `8h`, `12h`, `1d`, `3d`, `1w`, `1M`.
- `1M` artinya 1 bulan dan harus huruf M besar. Bot juga menerima alias `1mo`.
- Coin yang sudah diberi alias umum: BTC, ETH, BNB, SOL, XRP, ADA, DOGE, DOT, TRX, LTC, BCH, LINK, AVAX, TON, SHIB, PEPE, USDT, USDC.
- Untuk coin lain, coba kirim symbol Binance-nya, misalnya `/price btc`, `/price eth`, atau `/price btcusdt`.
