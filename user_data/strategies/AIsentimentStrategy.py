# pragma pylint: disable=missing-docstring
"""
AIsentimentStrategy.py — Strategia Freqtrade con AI Sentiment Analysis

Funzionamento:
1. bot_loop_start() → ogni ciclo (60s) aggiorna news + sentiment LLM (cache 10min)
2. populate_indicators() → calcola RSI, EMA, BBands su ogni coppia
3. populate_entry_trend() → BUY se sentiment BUY + RSI ≥ 55 + EMA confermante
4. populate_exit_trend() → SELL se sentiment SELL o RSI > 78 (overbought)
5. confirm_trade_entry() → log dettagliato + verifica freschezza del sentiment

Deployment: funziona in dry_run (paper) e live.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from pandas import DataFrame

# Aggiunge user_data/strategies al path per import helpers
_STRATEGY_DIR = Path(__file__).parent
if str(_STRATEGY_DIR) not in sys.path:
    sys.path.insert(0, str(_STRATEGY_DIR))

from freqtrade.strategy import IStrategy, Trade, Order, informative
from freqtrade.strategy import timeframe_to_minutes

import talib.abstract as ta
from technical import qtpylib

from helpers.news_client import CryptoCompareClient
from helpers.llm_client import LLMSentimentClient, Decision, SentimentResult, HOLD_FALLBACK

logger = logging.getLogger(__name__)


class AIsentimentStrategy(IStrategy):
    """
    Strategia AI-native che combina:
    - LLM Sentiment Analysis (OpenRouter primary / Gemini fallback)
    - Analisi tecnica classica (RSI ≥ 55 come filtro obbligatorio)
    - Multi-pair market scan tramite VolumePairList (top 30 USDT)
    - Notifiche Telegram native via configurazione Freqtrade
    """

    INTERFACE_VERSION = 3

    # ── Parametri strategia ───────────────────────────────────────────────────

    # Solo long (no short) — cambia can_short=True per abilitare
    can_short: bool = False

    # ROI: esci in profitto dopo X minuti
    minimal_roi = {
        "180": 0.01,    # +1% dopo 3h → garantisce uscita minima
        "60":  0.025,   # +2.5% dopo 1h
        "30":  0.04,    # +4% dopo 30min
        "0":   0.08,    # +8% immediato
    }

    # Stoploss fisso: -6% (mai perdere più di questo per trade)
    stoploss = -0.06

    # Trailing stoploss: blocca i profitti man mano che salgono
    trailing_stop = True
    trailing_stop_positive = 0.02          # Attiva il trailing dopo +2%
    trailing_stop_positive_offset = 0.035  # Trailing parte da +3.5%
    trailing_only_offset_is_reached = True

    # Timeframe principale: 5 minuti
    timeframe = "5m"

    # Non ricalcolare indicatori se non c'è una nuova candela
    process_only_new_candles = True

    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # Candele di warmup per indicatori (EMA 30 richiede almeno 30 candele)
    startup_candle_count: int = 50

    # Ordini: market per velocità di esecuzione
    order_types = {
        "entry": "market",
        "exit": "market",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }
    order_time_in_force = {"entry": "GTC", "exit": "GTC"}

    # ── Configurazione sentiment ───────────────────────────────────────────────

    # Soglia RSI minima per considerare un BUY (momentum richiesto)
    RSI_BUY_THRESHOLD: int = 55
    # Soglia RSI massima per uscita forzata (overbought)
    RSI_SELL_THRESHOLD: int = 78
    # Confidence minima LLM per agire
    CONFIDENCE_THRESHOLD: float = 0.68
    # Età massima del sentiment (minuti) prima di bloccare i trade
    MAX_SENTIMENT_AGE_MINUTES: float = 12.0

    # ── Stato interno (condiviso tra pair ma thread-safe tramite GIL) ─────────
    _news_client: Optional[CryptoCompareClient] = None
    _llm_client: Optional[LLMSentimentClient] = None
    _current_sentiment: SentimentResult = HOLD_FALLBACK

    # ─────────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ─────────────────────────────────────────────────────────────────────────

    def bot_start(self, **kwargs) -> None:
        """Inizializzazione dei client al primo avvio del bot."""
        logger.info("🤖 [AIsentiment] Inizializzazione client LLM e News...")
        self._news_client = CryptoCompareClient()
        self._llm_client = LLMSentimentClient()
        logger.info("✅ [AIsentiment] Bot pronto. Sentiment iniziale: HOLD (attesa primo ciclo)")

    def bot_loop_start(self, current_time: datetime, **kwargs) -> None:
        """
        Chiamato ad ogni ciclo del bot (ogni ~60s).
        Aggiorna news e sentiment — la cache interna garantisce max 1 chiamata API
        ogni 10 minuti, indipendentemente dal numero di pair nel whitelist.
        """
        if not self._news_client or not self._llm_client:
            return

        # Stima liquidità e trade aperti per contestualizzare il prompt LLM
        try:
            wallet_usdt = float(self.wallets.get_free(self.config.get("stake_currency", "USDT")))
        except Exception:
            wallet_usdt = 10000.0

        try:
            open_trades = len(Trade.get_open_trades())
        except Exception:
            open_trades = 0

        # Fetch news (cache 10min) — UNA sola chiamata per tutti i pair
        headlines = self._news_client.get_latest_headlines(
            categories="BTC,ETH,Altcoin,Market,Trading"
        )

        # Analisi sentiment (cache 10min) — UNA sola chiamata LLM
        self._current_sentiment = self._llm_client.analyze_sentiment(
            headlines=headlines,
            wallet_usdt=wallet_usdt,
            open_trades=open_trades,
        )

        # Log condensato ogni ciclo
        s = self._current_sentiment
        age = self._llm_client.get_cache_age_minutes()
        age_str = f"{age:.1f}min" if age is not None else "fresh"
        logger.info(
            f"📰 Sentiment: {s.decision.value} conf={s.confidence:.0%} "
            f"[{s.provider}] age={age_str} | "
            f"Headlines: {len(headlines)} | Trades aperti: {open_trades}"
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Indicatori Tecnici
    # ─────────────────────────────────────────────────────────────────────────

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        Calcola gli indicatori TA usati come filtro secondario al sentiment.
        RSI ≥ 55 è OBBLIGATORIO per il BUY (user requirement).
        """
        # RSI (14 periodi) — filtro momentum principale
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)

        # EMA 10 e 30 — direzione del trend
        dataframe["ema10"] = ta.EMA(dataframe, timeperiod=10)
        dataframe["ema30"] = ta.EMA(dataframe, timeperiod=30)

        # MACD — conferma momentum
        macd = ta.MACD(dataframe, fastperiod=12, slowperiod=26, signalperiod=9)
        dataframe["macd"] = macd["macd"]
        dataframe["macdsignal"] = macd["macdsignal"]
        dataframe["macd_hist"] = macd["macdhist"]

        # Bollinger Bands — volatilità e livelli dinamici
        bollinger = qtpylib.bollinger_bands(qtpylib.typical_price(dataframe), window=20, stds=2)
        dataframe["bb_lower"] = bollinger["lower"]
        dataframe["bb_mid"] = bollinger["mid"]
        dataframe["bb_upper"] = bollinger["upper"]

        # Volume medio (20 candele) — filtro liquidità
        dataframe["volume_mean"] = dataframe["volume"].rolling(20).mean()

        # ATR — volatilità assoluta (utile per position sizing futuro)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)

        return dataframe

    # ─────────────────────────────────────────────────────────────────────────
    # Segnali Entrata
    # ─────────────────────────────────────────────────────────────────────────

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        Condizioni BUY (TUTTE devono essere soddisfatte):
        1. Sentiment LLM = BUY con confidence ≥ 68%
        2. RSI ≥ 55 (momentum richiesto dall'utente)
        3. EMA10 > EMA30 (trend rialzista confermato)
        4. MACD hist positivo (momentum in crescita)
        5. Volume sopra media (liquidità sufficiente)
        """
        s = self._current_sentiment
        sentiment_buy = (
            s.decision == Decision.BUY
            and s.confidence >= self.CONFIDENCE_THRESHOLD
        )

        dataframe.loc[
            (
                sentiment_buy  # Sentiment AI positivo
                & (dataframe["rsi"] >= self.RSI_BUY_THRESHOLD)     # RSI ≥ 55
                & (dataframe["ema10"] > dataframe["ema30"])          # Trend rialzista
                & (dataframe["macd_hist"] > 0)                       # MACD positivo
                & (dataframe["volume"] > dataframe["volume_mean"] * 0.8)  # Volume OK
                & (dataframe["volume"] > 0)
            ),
            "enter_long",
        ] = 1

        return dataframe

    # ─────────────────────────────────────────────────────────────────────────
    # Segnali Uscita
    # ─────────────────────────────────────────────────────────────────────────

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        Condizioni SELL (una delle seguenti):
        1. Sentiment LLM = SELL con confidence ≥ 68%
        2. RSI ≥ 78 (overbought classico)
        3. EMA10 < EMA30 (inversione trend)
        """
        s = self._current_sentiment
        sentiment_sell = (
            s.decision == Decision.SELL
            and s.confidence >= self.CONFIDENCE_THRESHOLD
        )

        dataframe.loc[
            (
                sentiment_sell                              # AI dice SELL
                | (dataframe["rsi"] >= self.RSI_SELL_THRESHOLD)  # Overbought RSI
                | (                                         # Inversione trend
                    (dataframe["ema10"] < dataframe["ema30"])
                    & (dataframe["ema10"].shift(1) >= dataframe["ema30"].shift(1))
                )
            )
            & (dataframe["volume"] > 0),
            "exit_long",
        ] = 1

        return dataframe

    # ─────────────────────────────────────────────────────────────────────────
    # Hook pre-ordine — doppia verifica + log Telegram
    # ─────────────────────────────────────────────────────────────────────────

    def confirm_trade_entry(
        self,
        pair: str,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        current_time: datetime,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> bool:
        """
        Ultima verifica prima dell'ordine:
        - Blocca se il sentiment è troppo vecchio (dati LLM stantii)
        - Logga il reasoning LLM per audit trail
        """
        s = self._current_sentiment
        age = self._llm_client.get_cache_age_minutes() if self._llm_client else None

        # Blocca se sentiment è troppo vecchio
        if age is not None and age > self.MAX_SENTIMENT_AGE_MINUTES:
            logger.warning(
                f"⛔ [AIsentiment] Trade su {pair} BLOCCATO: sentiment scaduto "
                f"({age:.1f} min > {self.MAX_SENTIMENT_AGE_MINUTES} min)"
            )
            return False

        # Log dettagliato che apparirà anche nei log Telegram
        logger.info(
            f"✅ [AIsentiment] ENTRY CONFERMATA\n"
            f"   Pair: {pair}\n"
            f"   Amount: {amount:.4f} | Rate: {rate:.4f}\n"
            f"   Sentiment: {s.decision.value} ({s.confidence:.0%}) via {s.provider}\n"
            f"   Reasoning: {s.reasoning}\n"
            f"   Sentiment age: {f'{age:.1f}min' if age else 'fresh'}"
        )
        return True

    def confirm_trade_exit(
        self,
        pair: str,
        trade: Trade,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        exit_reason: str,
        current_time: datetime,
        **kwargs,
    ) -> bool:
        """Log dell'uscita con P&L e motivazione."""
        profit_ratio = trade.calc_profit_ratio(rate)
        s = self._current_sentiment
        logger.info(
            f"💰 [AIsentiment] EXIT {pair}\n"
            f"   Motivo: {exit_reason}\n"
            f"   P&L: {profit_ratio:+.2%}\n"
            f"   Sentiment attuale: {s.decision.value} ({s.confidence:.0%})"
        )
        return True

    # ─────────────────────────────────────────────────────────────────────────
    # Plot config (per analisi backtest)
    # ─────────────────────────────────────────────────────────────────────────

    plot_config = {
        "main_plot": {
            "ema10": {"color": "cyan"},
            "ema30": {"color": "orange"},
            "bb_lower": {"color": "rgba(100,100,255,0.3)"},
            "bb_upper": {"color": "rgba(100,100,255,0.3)"},
            "bb_mid": {"color": "rgba(100,100,255,0.5)"},
        },
        "subplots": {
            "RSI": {
                "rsi": {"color": "purple"},
            },
            "MACD": {
                "macd": {"color": "blue"},
                "macdsignal": {"color": "red"},
                "macd_hist": {"color": "rgba(0,200,0,0.5)", "type": "bar"},
            },
        },
    }
