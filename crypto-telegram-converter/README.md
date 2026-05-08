# Crypto Telegram Converter Bot

Bot Telegram sederhana untuk cek harga crypto, konversi, dan candle timeframe secara real-time memakai Binance Spot public API.

## Fitur

- `/price btc` menampilkan harga BTC ke USD dan IDR.
- `/price eth idr` menampilkan harga ETH ke IDR.
- `/p btc` menampilkan ringkasan harga, H/L, perubahan 1h/24h/7d/30d, ATH, volume, dan market cap.
- `/gas` menampilkan gas Ethereum realtime dalam gwei.
- `/convert 0.5 btc usd` mengonversi jumlah coin ke USD.
- `/convert 250 doge idr` mengonversi jumlah coin ke IDR.
- `/tv eth` mengirim gambar chart ETHUSDT timeframe default 1h.
- `/tv eth 15m` mengirim gambar chart ETHUSDT timeframe 15 menit.
- `/kline btc 15m` menampilkan candle BTCUSDT timeframe 15 menit dalam bentuk teks.
- `/timeframes` menampilkan daftar timeframe Binance yang didukung.
- Format cepat: `btc`, `eth idr`, `btc 15m`, `eth 30m`, `0.1 btc to idr`.

## Cara Menjalankan

1. Buat bot Telegram lewat [BotFather](https://t.me/BotFather), lalu salin token bot.
2. Salin file `.env.example` menjadi `.env`.
3. Isi token:

```env
TELEGRAM_BOT_TOKEN=token_dari_botfather
```

Jika VPS kamu mendapat error Binance `451 restricted location`, coba tambahkan endpoint market-data Binance di `.env`:

```env
BINANCE_API_BASES=https://data-api.binance.vision,https://api.binance.com,https://api1.binance.com,https://api2.binance.com,https://api3.binance.com,https://api4.binance.com
```

Opsional untuk `/gas`: isi API key Etherscan kalau kamu punya. Kalau tidak diisi, bot memakai public Ethereum RPC.

```env
ETHERSCAN_API_KEY=isi_api_key_etherscan
ETH_RPC_URLS=https://ethereum.publicnode.com,https://rpc.flashbots.net,https://cloudflare-eth.com
```

4. Install dependency chart:

```bash
pip3 install -r requirements.txt
```

Kalau `pip3` belum ada:

```bash
sudo apt update
sudo apt install -y python3-pip
pip3 install -r requirements.txt
```

5. Jalankan bot:

```bash
python3 bot.py
```

6. Buka chat bot Telegram kamu, lalu kirim `/start`.

## Catatan

- Bot ini memakai long polling, jadi cocok untuk dijalankan di laptop/VPS tanpa setup webhook.
- Harga dan candle berasal dari Binance Spot public API dan bisa berubah cepat. Public API punya rate limit, jadi hindari spam request terlalu banyak.
- Command `/p` memakai CoinGecko untuk data market lengkap seperti ATH, market cap, volume, dan perubahan 7d/30d.
- Command `/gas` memakai Etherscan Gas Oracle jika `ETHERSCAN_API_KEY` diisi, lalu fallback ke public Ethereum RPC `eth_feeHistory`.
- Bot mencoba beberapa endpoint Binance secara berurutan, termasuk `data-api.binance.vision` untuk market data.
- Chart dibuat lokal di VPS dengan `matplotlib`, lalu dikirim ke Telegram sebagai foto.
- Harga USD memakai pair USDT Binance, misalnya BTCUSDT.
- Timeframe Binance yang didukung: `1s`, `1m`, `3m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `6h`, `8h`, `12h`, `1d`, `3d`, `1w`, `1M`.
- `1M` artinya 1 bulan dan harus huruf M besar. Bot juga menerima alias `1mo`.
- Coin yang sudah diberi alias umum: BTC, ETH, BNB, SOL, XRP, ADA, DOGE, DOT, TRX, LTC, BCH, LINK, AVAX, TON, SHIB, PEPE, USDT, USDC.
- Untuk coin lain, coba kirim symbol Binance-nya, misalnya `/price btc`, `/price eth`, atau `/price btcusdt`.
