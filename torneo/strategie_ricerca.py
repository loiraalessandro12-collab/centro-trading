"""Le strategie PROMOSSE dalla ricerca di ogni notte (ricerca_giornaliera/PROCEDURA.md), nella forma che usa la gara
dal vivo: una funzione che riceve i dati {mercato: candele} e restituisce i PESI (giorno × mercato).

Ogni regola qui è la copia esatta di quella provata sul banco (torneo/banco.py), con i parametri scelti
sull'allenamento: NON si cambiano dopo l'entrata in gara (azzererebbe la prova dal vivo). Lo script della prova
è indicato accanto a ogni funzione.
"""
from __future__ import annotations

import pandas as pd


def _su_calendario(s: pd.Series, idx: pd.DatetimeIndex) -> pd.Series:
    return s.astype(float).reindex(s.index.union(idx)).ffill().reindex(idx).fillna(0.0)


def nasdaq_leva_filtro(d: dict, strumento: str = "LQQ_EUR", segnale: str = "QQQ_USD", n: int = 150) -> pd.DataFrame:
    """Nasdaq 100 a leva 2 (ETF europeo LQQ) quando il QQQ in dollari chiude sopra la sua media a `n` giorni di borsa,
    altrimenti liquidità all'€STR. Prova: torneo/ricerca/2026-11-08_nasdaq_leva.py e _ucits.py (promossa l'8/11/2026)."""
    s = d[segnale]["close"].astype(float)
    media = s.rolling(n, min_periods=n).mean()
    dentro = ((s > media) & media.notna()).astype(float)
    idx = d[strumento].index
    dentro = _su_calendario(dentro, idx) > 0.5
    W = pd.DataFrame(0.0, index=idx, columns=[strumento, "LIQ"])
    W.loc[dentro, strumento] = 1.0
    W.loc[~dentro, "LIQ"] = 1.0
    return W
