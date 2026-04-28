# 🤖 AIsentimentBot — Freqtrade + AI Sentiment Strategy

> Fork di [freqtrade/freqtrade](https://github.com/freqtrade/freqtrade) con una strategia custom AI-native che combina **LLM Sentiment Analysis** e **Analisi Tecnica** per fare trading autonomo su Kraken.

---

## 📋 Descrizione

Il bot opera in ciclo continuo 24/7 su **Kraken** (paper trading e live), analizzando in tempo reale:

1. **News crypto** via CryptoCompare API — aggiornate ogni 10 minuti
2. **Sentiment LLM** via OpenRouter (Llama 3 8B, gratuito) con fallback su Google Gemini
3. **Indicatori tecnici**: RSI ≥ 55 (obbligatorio), EMA 10/30, MACD, Bollinger Bands

Il risultato è una decisione autonoma di `BUY / SELL / HOLD` con notifiche Telegram.

---

## 🏗️ Architettura

```
docker-compose.yml
└── freqtrade_ai (container)
    ├── user_data/config.json          ← Configurazione exchange + dashboard
    └── user_data/strategies/
        ├── AIsentimentStrategy.py     ← Strategia principale
        └── helpers/
            ├── news_client.py         ← Fetch news CryptoCompare (cache 10min)
            └── llm_client.py          ← OpenRouter + Gemini fallback (cache 10min)
```

### Stack

| Componente | Tecnologia |
|---|---|
| Framework | [Freqtrade](https://www.freqtrade.io) (Python) |
| Exchange | Kraken via CCXT |
| News | CryptoCompare API v2 |
| LLM Primario | OpenRouter — `meta-llama/llama-3-8b-instruct:free` |
| LLM Fallback | Google Gemini 1.5 Flash |
| Dashboard | FreqUI (React, porta 8080) |
| Database | SQLite (trade history persistente) |
| Notifiche | Telegram Bot API |
| Deployment | Docker / AWS EC2 |

---

## ⚙️ Configurazione

### 1. Crea il file `.env`

Copia il template e inserisci le tue chiavi API:

```env
# Kraken (solo per live trading, non serve in dry_run)
KRAKEN_API_KEY=la_tua_chiave
KRAKEN_API_SECRET=il_tuo_secret

# Telegram — crea il bot con @BotFather
TELEGRAM_BOT_TOKEN=il_tuo_token
TELEGRAM_CHAT_ID=il_tuo_chat_id

# News
CRYPTOCOMPARE_API_KEY=la_tua_chiave   # https://min-api.cryptocompare.com/

# LLM (almeno uno obbligatorio)
OPENROUTER_API_KEY=la_tua_chiave      # https://openrouter.ai/ (gratis)
GEMINI_API_KEY=la_tua_chiave          # https://aistudio.google.com/ (fallback)

# Dashboard FreqUI
FREQTRADE_UI_USERNAME=admin
FREQTRADE_UI_PASSWORD=freqtrade2024!
```

### 2. Aggiorna `user_data/config.json`

Nella sezione `"telegram"` inserisci token e chat_id:

```json
"telegram": {
    "enabled": true,
    "token": "IL_TUO_TOKEN",
    "chat_id": "IL_TUO_CHAT_ID"
}
```

---

## 🚀 Avvio

### Metodo 1 — Docker (raccomandato, per AWS/VPS o uso locale)

```bash
# Prima build (solo la prima volta o dopo modifiche al Dockerfile)
docker compose build

# Avvio in background
docker compose up -d

# Controlla i log in tempo reale
docker logs freqtrade_ai -f

# Stop
docker compose down
```

### Metodo 2 — AWS EC2 (persistente)

```bash
# Sul server, clona la repo
git clone <il_tuo_repo>
cd freqtrade

# Crea e compila il .env
cp .env .env  # poi modifica con nano/vim

# Avvia con Docker (sempre attivo, si riavvia automaticamente)
docker compose up -d
```

Il container è configurato con `restart: unless-stopped` — si riavvia automaticamente dopo riavvii del server.

### Aggiornare la strategia senza rebuild

Dato che `user_data/` è montato come volume, modifiche ai file Python nella strategia vengono recepite con un semplice restart:

```bash
docker compose restart
```

---

## 📊 Dashboard — FreqUI

Apri il browser su: **http://localhost:8080** (o `http://IP_DEL_SERVER:8080` su AWS)

| Campo | Valore |
|---|---|
| Username | `admin` (o il valore in `.env`) |
| Password | `freqtrade2024!` (o il valore in `.env`) |

### Sezioni disponibili

- **Dashboard** — panoramica: saldo, trade aperti, P&L, grafici
- **Trade** — lista trade aperti e chiusi con dettagli
- **Chart** — candlestick con overlay degli indicatori (RSI, EMA, MACD)
- **Logs** — log in tempo reale del bot (utile per debug)

---

## 📈 Logica della Strategia

### Ciclo di esecuzione (ogni 60 secondi)

```
bot_loop_start()
 ├── Fetch news CryptoCompare  (cache 10min — 1 chiamata per tutti i pair)
 └── LLM Sentiment Analysis    (cache 10min — 1 chiamata per tutti i pair)
      └── OpenRouter Llama 3  →  se fallisce  →  Gemini Flash

populate_entry_trend()  [per ogni coppia]
 ├── Sentiment = BUY  AND  confidence ≥ 68%
 ├── RSI ≥ 55                  (momentum obbligatorio)
 ├── EMA10 > EMA30             (trend rialzista)
 ├── MACD histogram > 0        (momentum in crescita)
 └── Volume > media 20 candele

populate_exit_trend()  [per ogni coppia]
 ├── Sentiment = SELL  AND  confidence ≥ 68%
 ├── RSI ≥ 78                  (overbought classico)
 └── EMA10 < EMA30  (crossunder — inversione trend)
```

### Risk Management

| Parametro | Valore |
|---|---|
| Stoploss fisso | -6% |
| Trailing stop | Attivo dopo +2%, parte da +3.5% |
| Max trade aperti | 4 |
| ROI target | +8% immediato / +4% dopo 30min / +2.5% dopo 1h / +1% dopo 3h |
| Pair scanning | Top 30 USDT per volume su Kraken (aggiornato ogni 30min) |

---

## 📱 Comandi Telegram

| Comando | Descrizione |
|---|---|
| `/start` | Avvia il trading |
| `/stop` | Ferma il trading (non chiude trade aperti) |
| `/status` | Mostra tutti i trade aperti |
| `/profit` | P&L cumulativo |
| `/balance` | Saldo per valuta |
| `/forceexit <id>\|all` | Chiude forzatamente uno o tutti i trade |
| `/performance` | Performance per coppia |
| `/daily` | P&L degli ultimi giorni |

---

## 🔒 Sicurezza

> ⚠️ **Non committare mai il file `.env`** — è già in `.gitignore`.
>
> Su AWS, limita l'accesso alla porta 8080 via Security Group solo al tuo IP.

---

## 📁 File principali

| File | Scopo |
|---|---|
| `.env` | API keys (NON committare) |
| `docker-compose.yml` | Configurazione Docker |
| `docker/Dockerfile.custom` | Immagine custom con python-dotenv |
| `user_data/config.json` | Config exchange, dashboard, Telegram |
| `user_data/strategies/AIsentimentStrategy.py` | Strategia principale |
| `user_data/strategies/helpers/news_client.py` | Client CryptoCompare |
| `user_data/strategies/helpers/llm_client.py` | Client OpenRouter + Gemini |

---

## 🌐 Passare al Live Trading

1. Nel `.env` inserisci le chiavi Kraken reali
2. In `user_data/config.json` imposta `"dry_run": false`
3. Riavvia: `docker compose restart`

> ⚠️ **Testa sempre in dry_run prima di mettere soldi reali.**

---

---

# ![freqtrade](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/docs/assets/freqtrade_poweredby.svg)

[![Freqtrade CI](https://github.com/freqtrade/freqtrade/actions/workflows/ci.yml/badge.svg?branch=develop)](https://github.com/freqtrade/freqtrade/actions/workflows/ci.yml)
[![DOI](https://joss.theoj.org/papers/10.21105/joss.04864/status.svg)](https://doi.org/10.21105/joss.04864)
[![codecov](https://codecov.io/gh/freqtrade/freqtrade/branch/develop/graph/badge.svg?token=AD5BG3ATKI)](https://codecov.io/gh/freqtrade/freqtrade)
[![Documentation](https://readthedocs.org/projects/freqtrade/badge/)](https://www.freqtrade.io)
[![Discord Server](https://img.shields.io/badge/Freqtrade_Discord-4E4E4E?logo=discord)](https://discord.gg/p7nuUNVfP7)

Freqtrade is a free and open source crypto trading bot written in Python. It is designed to support all major exchanges and be controlled via Telegram or webUI. It contains backtesting, plotting and money management tools as well as strategy optimization by machine learning.

![freqtrade](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/docs/assets/freqtrade-screenshot.png)

## Disclaimer

This software is for educational purposes only. Do not risk money which
you are afraid to lose. USE THE SOFTWARE AT YOUR OWN RISK. THE AUTHORS
AND ALL AFFILIATES ASSUME NO RESPONSIBILITY FOR YOUR TRADING RESULTS.

Always start by running a trading bot in Dry-Run and do not engage money
before you understand how it works and what profit/loss you should
expect.

We strongly recommend you to have coding and Python knowledge. Do not
hesitate to read the source code and understand the mechanism of this bot.

## Supported Exchange marketplaces

Please read the [exchange-specific notes](https://www.freqtrade.io/en/stable/exchanges/) to learn about special configurations that maybe needed for each exchange.

### Supported Spot Exchanges

- [X] [Binance](https://www.binance.com/)
- [X] [BingX](https://bingx.com/invite/0EM9RX)
- [X] [Bitget](https://www.bitget.com/)
- [X] [Bitmart](https://bitmart.com/)
- [X] [Bybit](https://bybit.com/)
- [X] [Gate.io](https://www.gate.io/ref/6266643)
- [X] [HTX](https://www.htx.com/)
- [X] [Hyperliquid](https://hyperliquid.xyz/) (A decentralized exchange, or DEX)
- [X] [Kraken](https://kraken.com/)
- [X] [OKX](https://okx.com/)
- [X] [MyOKX](https://okx.com/) (OKX EEA)
- [ ] [potentially many others](https://github.com/ccxt/ccxt/). _(We cannot guarantee they will work)_

### Supported Futures Exchanges

- [X] [Binance](https://www.binance.com/)
- [X] [Bitget](https://www.bitget.com/)
- [X] [Gate.io](https://www.gate.io/ref/6266643)
- [X] [Hyperliquid](https://hyperliquid.xyz/) (A decentralized exchange, or DEX)
- [X] [OKX](https://okx.com/)
- [X] [Bybit](https://bybit.com/)
- [X] [Kraken](https://www.kraken.com/features/futures)

Please make sure to read the [exchange specific notes](https://www.freqtrade.io/en/stable/exchanges/), as well as the [trading with leverage](https://www.freqtrade.io/en/stable/leverage/) documentation before diving in.

### Community tested

Exchanges confirmed working by the community:

- [X] [Bitvavo](https://bitvavo.com/)
- [X] [Kucoin](https://www.kucoin.com/)

## Documentation

We invite you to read the bot documentation to ensure you understand how the bot is working.

Please find the complete documentation on the [freqtrade website](https://www.freqtrade.io).

## Features

- [x] **Based on Python 3.11+**: For botting on any operating system - Windows, macOS and Linux.
- [x] **Persistence**: Persistence is achieved through sqlite.
- [x] **Dry-run**: Run the bot without paying money.
- [x] **Backtesting**: Run a simulation of your buy/sell strategy.
- [x] **Strategy Optimization by machine learning**: Use machine learning to optimize your buy/sell strategy parameters with real exchange data.
- [X] **Adaptive prediction modeling**: Build a smart strategy with FreqAI that self-trains to the market via adaptive machine learning methods. [Learn more](https://www.freqtrade.io/en/stable/freqai/)
- [x] **Whitelist crypto-currencies**: Select which crypto-currency you want to trade or use dynamic whitelists.
- [x] **Blacklist crypto-currencies**: Select which crypto-currency you want to avoid.
- [x] **Builtin WebUI**: Builtin web UI to manage your bot.
- [x] **Manageable via Telegram**: Manage the bot with Telegram.
- [x] **Display profit/loss in fiat**: Display your profit/loss in fiat currency.
- [x] **Performance status report**: Provide a performance status of your current trades.

## Quick start

Please refer to the [Docker Quickstart documentation](https://www.freqtrade.io/en/stable/docker_quickstart/) on how to get started quickly.

For further (native) installation methods, please refer to the [Installation documentation page](https://www.freqtrade.io/en/stable/installation/).

## Basic Usage

### Bot commands

```
usage: freqtrade [-h] [-V]
                 {trade,create-userdir,new-config,show-config,new-strategy,download-data,convert-data,convert-trade-data,trades-to-ohlcv,list-data,backtesting,backtesting-show,backtesting-analysis,edge,hyperopt,hyperopt-list,hyperopt-show,list-exchanges,list-markets,list-pairs,list-strategies,list-hyperoptloss,list-freqaimodels,list-timeframes,show-trades,test-pairlist,convert-db,install-ui,plot-dataframe,plot-profit,webserver,strategy-updater,lookahead-analysis,recursive-analysis}
                 ...

Free, open source crypto trading bot

positional arguments:
  {trade,create-userdir,new-config,show-config,new-strategy,download-data,convert-data,convert-trade-data,trades-to-ohlcv,list-data,backtesting,backtesting-show,backtesting-analysis,edge,hyperopt,hyperopt-list,hyperopt-show,list-exchanges,list-markets,list-pairs,list-strategies,list-hyperoptloss,list-freqaimodels,list-timeframes,show-trades,test-pairlist,convert-db,install-ui,plot-dataframe,plot-profit,webserver,strategy-updater,lookahead-analysis,recursive-analysis}
    trade               Trade module.
    create-userdir      Create user-data directory.
    new-config          Create new config
    show-config         Show resolved config
    new-strategy        Create new strategy
    download-data       Download backtesting data.
    convert-data        Convert candle (OHLCV) data from one format to
                        another.
    convert-trade-data  Convert trade data from one format to another.
    trades-to-ohlcv     Convert trade data to OHLCV data.
    list-data           List downloaded data.
    backtesting         Backtesting module.
    backtesting-show    Show past Backtest results
    backtesting-analysis
                        Backtest Analysis module.
    hyperopt            Hyperopt module.
    hyperopt-list       List Hyperopt results
    hyperopt-show       Show details of Hyperopt results
    list-exchanges      Print available exchanges.
    list-markets        Print markets on exchange.
    list-pairs          Print pairs on exchange.
    list-strategies     Print available strategies.
    list-hyperoptloss   Print available hyperopt loss functions.
    list-freqaimodels   Print available freqAI models.
    list-timeframes     Print available timeframes for the exchange.
    show-trades         Show trades.
    test-pairlist       Test your pairlist configuration.
    convert-db          Migrate database to different system
    install-ui          Install FreqUI
    plot-dataframe      Plot candles with indicators.
    plot-profit         Generate plot showing profits.
    webserver           Webserver module.
    strategy-updater    updates outdated strategy files to the current version
    lookahead-analysis  Check for potential look ahead bias.
    recursive-analysis  Check for potential recursive formula issue.

options:
  -h, --help            show this help message and exit
  -V, --version         show program's version number and exit
```

### Telegram RPC commands

Telegram is not mandatory. However, this is a great way to control your bot. More details and the full command list on the [documentation](https://www.freqtrade.io/en/stable/telegram-usage/)

- `/start`: Starts the trader.
- `/stop`: Stops the trader.
- `/stopentry`: Stop entering new trades.
- `/status <trade_id>|[table]`: Lists all or specific open trades.
- `/profit [<n>]`: Lists cumulative profit from all finished trades, over the last n days.
- `/profit_long [<n>]`: Lists cumulative profit from all finished long trades, over the last n days.
- `/profit_short [<n>]`: Lists cumulative profit from all finished short trades, over the last n days.
- `/forceexit <trade_id>|all`: Instantly exits the given trade (Ignoring `minimum_roi`).
- `/fx <trade_id>|all`: Alias to `/forceexit`
- `/performance`: Show performance of each finished trade grouped by pair
- `/balance`: Show account balance per currency.
- `/daily <n>`: Shows profit or loss per day, over the last n days.
- `/help`: Show help message.
- `/version`: Show version.


## Development branches

The project is currently setup in two main branches:

- `develop` - This branch has often new features, but might also contain breaking changes. We try hard to keep this branch as stable as possible.
- `stable` - This branch contains the latest stable release. This branch is generally well tested.
- `feat/*` - These are feature branches, which are being worked on heavily. Please don't use these unless you want to test a specific feature.

## Support

### Help / Discord

For any questions not covered by the documentation or for further information about the bot, or to simply engage with like-minded individuals, we encourage you to join the Freqtrade [discord server](https://discord.gg/p7nuUNVfP7).

### [Bugs / Issues](https://github.com/freqtrade/freqtrade/issues?q=is%3Aissue)

If you discover a bug in the bot, please
[search the issue tracker](https://github.com/freqtrade/freqtrade/issues?q=is%3Aissue)
first. If it hasn't been reported, please
[create a new issue](https://github.com/freqtrade/freqtrade/issues/new/choose) and
ensure you follow the template guide so that the team can assist you as
quickly as possible.

For every [issue](https://github.com/freqtrade/freqtrade/issues/new/choose) created, kindly follow up and mark satisfaction or reminder to close issue when equilibrium ground is reached.

--Maintain github's [community policy](https://docs.github.com/en/site-policy/github-terms/github-community-code-of-conduct)--

### [Feature Requests](https://github.com/freqtrade/freqtrade/labels/enhancement)

Have you a great idea to improve the bot you want to share? Please,
first search if this feature was not [already discussed](https://github.com/freqtrade/freqtrade/labels/enhancement).
If it hasn't been requested, please
[create a new request](https://github.com/freqtrade/freqtrade/issues/new/choose)
and ensure you follow the template guide so that it does not get lost
in the bug reports.

### [Pull Requests](https://github.com/freqtrade/freqtrade/pulls)

Feel like the bot is missing a feature? We welcome your pull requests!

Please read the
[Contributing document](https://github.com/freqtrade/freqtrade/blob/develop/CONTRIBUTING.md)
to understand the requirements before sending your pull-requests.

Coding is not a necessity to contribute - maybe start with improving the documentation?
Issues labeled [good first issue](https://github.com/freqtrade/freqtrade/labels/good%20first%20issue) can be good first contributions, and will help get you familiar with the codebase.

**Note** before starting any major new feature work, *please open an issue describing what you are planning to do* or talk to us on [discord](https://discord.gg/p7nuUNVfP7) (please use the #dev channel for this). This will ensure that interested parties can give valuable feedback on the feature, and let others know that you are working on it.

**Important:** Always create your PR against the `develop` branch, not `stable`.

## Requirements

### Up-to-date clock

The clock must be accurate, synchronized to a NTP server very frequently to avoid problems with communication to the exchanges.

### Minimum hardware required

To run this bot we recommend you a cloud instance with a minimum of:

- Minimal (advised) system requirements: 2GB RAM, 1GB disk space, 2vCPU

### Software requirements

- [Python >= 3.11](http://docs.python-guide.org/en/latest/starting/installation/)
- [pip](https://pip.pypa.io/en/stable/installing/)
- [git](https://git-scm.com/book/en/v2/Getting-Started-Installing-Git)
- [TA-Lib](https://ta-lib.github.io/ta-lib-python/)
- [virtualenv](https://virtualenv.pypa.io/en/stable/installation.html) (Recommended)
- [Docker](https://www.docker.com/products/docker) (Recommended)
