"""
llm_client.py — Client LLM per Sentiment Analysis con dual-provider.

Architettura:
- Provider primario:  OpenRouter (Llama 3 8B free) — zero costo
- Provider fallback:  Google Gemini (Flash 1.5) — alta capacità
- Cache: 10 minuti per ridurre le chiamate API
- Output sempre strutturato: { decision, confidence, reasoning }
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()  # Carica .env in sviluppo locale; no-op in Docker
except ImportError:
    pass  # In Docker le variabili arrivano da docker-compose environment


logger = logging.getLogger(__name__)

# ── Configurazione ────────────────────────────────────────────────────────────
OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "meta-llama/llama-3-8b-instruct:free"

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"

LLM_CACHE_MINUTES: int = 10          # Rinnova il sentiment ogni 10 minuti
REQUEST_TIMEOUT: int = 20            # LLM è più lento delle news API
MAX_RETRIES: int = 2


class Decision(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class SentimentResult:
    decision: Decision
    confidence: float          # 0.0 → 1.0
    reasoning: str
    provider: str              # "openrouter" | "gemini" | "fallback"
    fetched_at: float = 0.0

    def is_stale(self) -> bool:
        age_minutes = (time.monotonic() - self.fetched_at) / 60
        return age_minutes >= LLM_CACHE_MINUTES

    @property
    def is_bullish(self) -> bool:
        return self.decision == Decision.BUY and self.confidence >= 0.65

    @property
    def is_bearish(self) -> bool:
        return self.decision == Decision.SELL and self.confidence >= 0.65

    def __str__(self) -> str:
        return (
            f"[{self.provider.upper()}] {self.decision.value} "
            f"(conf={self.confidence:.0%}) — {self.reasoning[:120]}..."
        )


# Sentinel per "nessun dato disponibile"
HOLD_FALLBACK = SentimentResult(
    decision=Decision.HOLD,
    confidence=0.0,
    reasoning="Dati LLM non disponibili — HOLD per sicurezza.",
    provider="fallback",
)


def _build_prompt(headlines: list[str], wallet_usdt: float, open_trades: int) -> str:
    """
    Costruisce il prompt per l'LLM.
    Compatto ma contestuale: include portfolio per decisioni più accurate.
    """
    news_block = "\n".join(f"- {h}" for h in headlines) if headlines else "- Nessuna news disponibile"
    return f"""Sei un trader algoritmico esperto in criptovalute. Analizza queste notizie di mercato e il contesto del portfolio per decidere l'azione ottimale.

**NOTIZIE RECENTI (ultime 10 minuti):**
{news_block}

**CONTESTO PORTFOLIO:**
- Liquidità disponibile: ${wallet_usdt:.0f} USDT
- Trade aperti: {open_trades}

**COMPITO:** Rispondi SOLO con un JSON valido (nessun testo extra):
{{
  "decision": "BUY" | "SELL" | "HOLD",
  "confidence": <float 0.0-1.0>,
  "reasoning": "<motivazione in 1-2 frasi>"
}}

Regole:
- BUY: news prevalentemente positive, momentum rialzista, liquidità disponibile
- SELL: news negative, risk-off, mercato in calo strutturale
- HOLD: news ambigue, segnali contrastanti, incertezza elevata
- confidence >= 0.70 richiesta per BUY/SELL, altrimenti HOLD
"""


class LLMSentimentClient:
    """
    Client dual-provider per Sentiment Analysis.
    Thread-safe per l'uso dentro bot_loop_start() di Freqtrade.
    """

    def __init__(self) -> None:
        self._cache: Optional[SentimentResult] = None
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "FreqtradeAISentiment/1.0"})

        if not OPENROUTER_API_KEY and not GEMINI_API_KEY:
            logger.error(
                "[LLMClient] Nessuna API key LLM trovata! "
                "Imposta OPENROUTER_API_KEY o GEMINI_API_KEY nel .env"
            )

    def analyze_sentiment(
        self,
        headlines: list[str],
        wallet_usdt: float = 10000.0,
        open_trades: int = 0,
    ) -> SentimentResult:
        """
        Ritorna il sentiment attuale. Usa la cache se non scaduta.

        Args:
            headlines: Lista titoli news da news_client
            wallet_usdt: Liquidità disponibile (per contestualizzare il prompt)
            open_trades: Numero trade aperti

        Returns:
            SentimentResult con decision, confidence e reasoning
        """
        if self._cache and not self._cache.is_stale():
            logger.debug(
                f"[LLMClient] Cache valida: {self._cache.decision.value} "
                f"({self._cache.confidence:.0%})"
            )
            return self._cache

        if not headlines:
            logger.warning("[LLMClient] Nessuna headline — ritorno HOLD fallback")
            return HOLD_FALLBACK

        prompt = _build_prompt(headlines, wallet_usdt, open_trades)

        # Prova OpenRouter prima, poi Gemini come fallback
        result = None
        if OPENROUTER_API_KEY:
            result = self._call_openrouter(prompt)
        if result is None and GEMINI_API_KEY:
            logger.info("[LLMClient] OpenRouter fallito, provo Gemini...")
            result = self._call_gemini(prompt)

        if result is None:
            logger.error("[LLMClient] Entrambi i provider falliti. HOLD per sicurezza.")
            return HOLD_FALLBACK

        result.fetched_at = time.monotonic()
        self._cache = result
        logger.info(f"[LLMClient] Nuovo sentiment: {result}")
        return result

    # ── OpenRouter ────────────────────────────────────────────────────────────

    def _call_openrouter(self, prompt: str) -> Optional[SentimentResult]:
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/freqtrade/freqtrade",
        }
        payload = {
            "model": OPENROUTER_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,       # Bassa per output deterministico
            "max_tokens": 200,
            "response_format": {"type": "json_object"},
        }
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self._session.post(
                    OPENROUTER_URL, headers=headers, json=payload,
                    timeout=REQUEST_TIMEOUT
                )
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
                return self._parse_json_response(content, provider="openrouter")
            except requests.exceptions.RequestException as e:
                logger.warning(f"[LLMClient] OpenRouter attempt {attempt}: {e}")
            except (KeyError, IndexError) as e:
                logger.error(f"[LLMClient] OpenRouter parsing error: {e}")
                break
        return None

    # ── Gemini ────────────────────────────────────────────────────────────────

    def _call_gemini(self, prompt: str) -> Optional[SentimentResult]:
        url = f"{GEMINI_URL}?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 200,
                "responseMimeType": "application/json",
            },
        }
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self._session.post(url, json=payload, timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
                data = resp.json()
                content = data["candidates"][0]["content"]["parts"][0]["text"]
                return self._parse_json_response(content, provider="gemini")
            except requests.exceptions.RequestException as e:
                logger.warning(f"[LLMClient] Gemini attempt {attempt}: {e}")
            except (KeyError, IndexError) as e:
                logger.error(f"[LLMClient] Gemini parsing error: {e}")
                break
        return None

    # ── Parsing ───────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_json_response(content: str, provider: str) -> Optional[SentimentResult]:
        """Parsa la risposta JSON dell'LLM in un SentimentResult tipizzato."""
        try:
            # L'LLM a volte wrappa il JSON in backtick markdown — rimuoviamo
            content = content.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            data = json.loads(content)

            raw_decision = str(data.get("decision", "HOLD")).upper()
            decision = Decision(raw_decision) if raw_decision in Decision.__members__ else Decision.HOLD
            confidence = float(data.get("confidence", 0.0))
            confidence = max(0.0, min(1.0, confidence))
            reasoning = str(data.get("reasoning", "Nessuna motivazione fornita."))

            return SentimentResult(
                decision=decision,
                confidence=confidence,
                reasoning=reasoning,
                provider=provider,
            )
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.error(f"[LLMClient] Impossibile parsare JSON da {provider}: {e}\nContent: {content[:200]}")
            return None

    def get_cache_age_minutes(self) -> Optional[float]:
        """Età della cache in minuti, None se mai chiamato."""
        if not self._cache or self._cache.fetched_at == 0.0:
            return None
        return (time.monotonic() - self._cache.fetched_at) / 60

    def force_refresh(self) -> None:
        """Forza il refresh del sentiment al prossimo ciclo."""
        self._cache = None
        logger.info("[LLMClient] Cache invalidata manualmente.")
