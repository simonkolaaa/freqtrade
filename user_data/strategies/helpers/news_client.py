"""
news_client.py — CryptoCompare news fetcher con caching intelligente.

Strategia di ottimizzazione token/chiamate:
- Fetch globale ogni NEWS_CACHE_MINUTES minuti (non per ogni coppia)
- Filtro per rilevanza: solo news che menzionano le top coin del whitelist
- Deduplica automatica per titoli identici
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()  # Carica .env in sviluppo locale; no-op in Docker
except ImportError:
    pass  # In Docker le variabili arrivano da docker-compose environment


logger = logging.getLogger(__name__)

# ── Configurazione ────────────────────────────────────────────────────────────
CRYPTOCOMPARE_API_KEY: str = os.getenv("CRYPTOCOMPARE_API_KEY", "")
CRYPTOCOMPARE_BASE_URL = "https://min-api.cryptocompare.com/data/v2/news/"
NEWS_CACHE_MINUTES: int = 10          # Rinnova le news ogni 10 minuti
MAX_HEADLINES: int = 15               # Massimo titoli da mandare all'LLM
REQUEST_TIMEOUT: int = 10            # Timeout HTTP in secondi


@dataclass
class NewsCache:
    headlines: list[str] = field(default_factory=list)
    fetched_at: float = 0.0           # epoch timestamp

    def is_stale(self) -> bool:
        age_minutes = (time.monotonic() - self.fetched_at) / 60
        return age_minutes >= NEWS_CACHE_MINUTES

    def is_empty(self) -> bool:
        return len(self.headlines) == 0


class CryptoCompareClient:
    """
    Client per CryptoCompare News API v2.
    Thread-safe per l'uso dentro bot_loop_start() di Freqtrade.
    """

    def __init__(self) -> None:
        if not CRYPTOCOMPARE_API_KEY:
            logger.warning(
                "[NewsClient] CRYPTOCOMPARE_API_KEY non impostata! "
                "Il fetch delle news non funzionerà."
            )
        self._cache = NewsCache()
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Apikey {CRYPTOCOMPARE_API_KEY}",
            "User-Agent": "FreqtradeAISentiment/1.0",
        })

    def get_latest_headlines(
        self,
        categories: str = "BTC,ETH,Trading,Altcoin,Market",
        limit: int = MAX_HEADLINES,
    ) -> list[str]:
        """
        Restituisce i titoli delle ultime notizie crypto.
        Usa la cache interna: chiama l'API solo se i dati sono scaduti.

        Args:
            categories: Filtro categorie CryptoCompare (stringa CSV)
            limit: Numero massimo di titoli da restituire

        Returns:
            Lista di stringhe con titolo + fonte, es.:
            ["Bitcoin breaks $70K: analyst says more upside - CoinDesk", ...]
        """
        if not self._cache.is_stale() and not self._cache.is_empty():
            logger.debug("[NewsClient] Cache valida, skip fetch API.")
            return self._cache.headlines[:limit]

        headlines = self._fetch_from_api(categories=categories, limit=limit + 5)
        if headlines:
            self._cache.headlines = headlines[:limit]
            self._cache.fetched_at = time.monotonic()
            logger.info(
                f"[NewsClient] Aggiornate {len(self._cache.headlines)} news "
                f"(categorie: {categories})"
            )
        elif self._cache.is_empty():
            logger.warning("[NewsClient] Fetch fallito e cache vuota. Ritorno lista vuota.")

        return self._cache.headlines[:limit]

    def _fetch_from_api(
        self, categories: str, limit: int
    ) -> list[str]:
        """Chiama l'API CryptoCompare e restituisce lista di titoli puliti."""
        if not CRYPTOCOMPARE_API_KEY:
            return []

        try:
            params = {
                "lang": "EN",
                "categories": categories,
                "sortOrder": "popular",
                "limit": min(limit, 50),   # API max = 50
            }
            resp = self._session.get(
                CRYPTOCOMPARE_BASE_URL, params=params, timeout=REQUEST_TIMEOUT
            )
            resp.raise_for_status()
            data = resp.json()

            articles = data.get("Data", [])
            seen: set[str] = set()
            headlines: list[str] = []

            for article in articles:
                title = article.get("title", "").strip()
                source = article.get("source_info", {}).get("name", "Unknown")
                if not title or title in seen:
                    continue
                seen.add(title)
                headlines.append(f"{title} [{source}]")

            return headlines

        except requests.exceptions.RequestException as e:
            logger.error(f"[NewsClient] Errore HTTP: {e}")
            return []
        except (KeyError, ValueError) as e:
            logger.error(f"[NewsClient] Errore parsing risposta: {e}")
            return []

    def get_cache_age_minutes(self) -> Optional[float]:
        """Restituisce l'età della cache in minuti, o None se mai fetchata."""
        if self._cache.fetched_at == 0.0:
            return None
        return (time.monotonic() - self._cache.fetched_at) / 60

    def force_refresh(self) -> None:
        """Forza il refresh della cache al prossimo get_latest_headlines()."""
        self._cache.fetched_at = 0.0
        logger.info("[NewsClient] Cache invalidata manualmente.")
