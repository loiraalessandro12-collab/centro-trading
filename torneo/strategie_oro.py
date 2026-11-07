"""Strategie SOLO SULL'ORO, al rialzo (long) e al ribasso (short) - torneo dell'oro del 2026-11-06.

Ognuna restituisce il PESO desiderato a fine candela (vedi motore.py), usando solo i prezzi fino a
quella candela: +1 = tutto il capitale long, -1 = tutto il capitale short (scommessa al ribasso:
si può fare solo con un CFD), 0 = fuori.
Con lato="long" resta solo la parte long (gli short diventano «fuori»); con lato="short" solo la parte
short. Servono a misurare quanto guadagna, o perde, ciascuna delle due metà.

Le finestre (n, entrata, orizzonti...) sono in giorni di calendario, perché l'oro del centro (PAXG)
ha una candela anche il sabato e la domenica: 200 giorni di calendario sono circa 140 giorni di borsa.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from torneo.strategie import filtro_gaussiano, rsi, sma, ema


def _lato(w: np.ndarray, lato: str) -> np.ndarray:
    if lato == "long":
        return np.clip(w, 0.0, None)
    if lato == "short":
        return np.clip(w, None, 0.0)
    return w


def _out(m: str, w, idx, lato: str) -> pd.DataFrame:
    w = np.nan_to_num(np.asarray(w, dtype=float), nan=0.0)
    return pd.DataFrame({m: _lato(w, lato)}, index=idx)


def _segno(x: pd.Series) -> np.ndarray:
    """+1 sopra zero, -1 sotto, 0 se il dato non c'è ancora (riscaldamento)."""
    return np.sign(x.to_numpy(dtype=float))


def macchina(entra_long, esci_long, entra_short, esci_short) -> np.ndarray:
    """Posizione +1 / -1 / 0 dai segnali, candela per candela: si resta dentro finché arriva l'uscita;
    un'entrata dal lato opposto gira la posizione. Segnali contrari nella stessa candela si annullano."""
    el, xl, es, xs = (np.asarray(a, dtype=bool) for a in (entra_long, esci_long, entra_short, esci_short))
    w = np.zeros(len(el))
    pos = 0.0
    for i in range(len(el)):
        if pos > 0 and xl[i]:
            pos = 0.0
        elif pos < 0 and xs[i]:
            pos = 0.0
        if el[i] and not es[i]:
            pos = 1.0
        elif es[i] and not el[i]:
            pos = -1.0
        w[i] = pos
    return w


# --------------------------------------------------------------------------- tendenza
def ls_media(dati, m, n=200, lato="ls"):
    """Sopra la media a n giorni long, sotto short."""
    c = dati[m]["close"]
    return _out(m, _segno(c - sma(c, n)), c.index, lato)


def ls_incrocio(dati, m, veloce=20, lenta=100, lato="ls"):
    """Media veloce sopra la lenta: long; sotto: short."""
    c = dati[m]["close"]
    return _out(m, _segno(ema(c, veloce) - ema(c, lenta)), c.index, lato)


def ls_donchian(dati, m, entrata=55, uscita=20, lato="ls"):
    """Turtle in tutte e due le direzioni: long se la chiusura supera il massimo degli ultimi 'entrata'
    giorni, esce sotto il minimo degli ultimi 'uscita'; short al contrario."""
    d = dati[m]
    c, h, l = d["close"], d["high"], d["low"]
    hi_e = h.rolling(entrata, min_periods=entrata).max().shift(1)
    lo_e = l.rolling(entrata, min_periods=entrata).min().shift(1)
    hi_u = h.rolling(uscita, min_periods=uscita).max().shift(1)
    lo_u = l.rolling(uscita, min_periods=uscita).min().shift(1)
    w = macchina(c > hi_e, c < lo_u, c < lo_e, c > hi_u)
    return _out(m, w, c.index, lato)


def ls_momentum(dati, m, orizzonti=(30, 90, 180, 365), lato="ls"):
    """Momentum nel tempo: per ogni orizzonte +1 se l'oro è salito, -1 se è sceso; peso = media dei voti
    (da -1 a +1). È la regola dei fondi «trend following» (Moskowitz, Ooi e Pedersen, 2012)."""
    c = dati[m]["close"]
    voti = [np.sign(c / c.shift(L) - 1.0) for L in orizzonti]
    w = pd.concat(voti, axis=1).mean(axis=1, skipna=False)
    return _out(m, w.to_numpy(), c.index, lato)


def ls_gaussiano(dati, m, periodo=144, molt=1.414, uscita="filtro", lato="ls"):
    """Canale gaussiano (video Signum) nei due sensi: long se il filtro sale e la chiusura supera la banda
    alta; short se il filtro scende e la chiusura rompe la banda bassa. Esce quando la chiusura
    riattraversa il filtro (uscita='filtro') o rientra nel canale (uscita='banda')."""
    d = dati[m]
    src = ((d["high"] + d["low"] + d["close"]) / 3).to_numpy(dtype=float)
    cp = d["close"].shift(1)
    tr = pd.concat([d["high"] - d["low"], (d["high"] - cp).abs(), (d["low"] - cp).abs()], axis=1).max(axis=1)
    f = filtro_gaussiano(src, periodo)
    ftr = filtro_gaussiano(tr.to_numpy(dtype=float), periodo)
    alta, bassa = f + ftr * molt, f - ftr * molt
    c = d["close"].to_numpy(dtype=float)
    fp = np.concatenate([[np.nan], f[:-1]])
    with np.errstate(invalid="ignore"):
        sale, scende = f > fp, f < fp
        el, es = sale & (c > alta), scende & (c < bassa)
        if uscita == "filtro":
            xl, xs = c < f, c > f
        else:
            xl, xs = c < alta, c > bassa
    w = macchina(el, xl, es, xs)
    w[~np.isfinite(f)] = 0.0
    return _out(m, w, d.index, lato)


def ls_asimmetrica(dati, m, n=200, lb=90, lato="ls"):
    """Long sopra la media a n giorni; short solo se l'oro è sotto la media E in calo sugli ultimi 'lb'
    giorni (doppia conferma per il ribasso, perché nel lungo periodo l'oro tende a salire)."""
    c = dati[m]["close"]
    s = sma(c, n)
    giu = (c < s) & (c / c.shift(lb) - 1 < 0)
    w = np.where(c > s, 1.0, np.where(giu, -1.0, 0.0))
    w[s.isna().to_numpy() | c.shift(lb).isna().to_numpy()] = 0.0
    return _out(m, w, c.index, lato)


# --------------------------------------------------------------------------- protezione e miscele (torneo 2, 2026-11-07)
def atr(d: pd.DataFrame, n: int = 20) -> pd.Series:
    """ATR: l'escursione media di una candela (massimo meno minimo, contando anche i salti fra una
    chiusura e l'apertura dopo) sulle ultime n candele."""
    cp = d["close"].shift(1)
    tr = pd.concat([d["high"] - d["low"], (d["high"] - cp).abs(), (d["low"] - cp).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


def con_stop(W: pd.DataFrame, dati, m, k=3.0, n_atr=20) -> pd.DataFrame:
    """Protezione che segue il prezzo (chandelier stop): da long si esce se la chiusura scende sotto il
    massimo toccato dall'entrata meno k ATR; da short se sale sopra il minimo dall'entrata più k ATR.
    Dopo uno stop si resta fuori finché la regola di base non cambia verso (o torna a zero)."""
    d = dati[m]
    c = d["close"].reindex(W.index).to_numpy(dtype=float)
    a = atr(d, n_atr).reindex(W.index).to_numpy(dtype=float)
    base = W[m].to_numpy(dtype=float)
    out = np.zeros(len(base))
    verso, estremo, blocco = 0.0, np.nan, 0.0
    for i in range(len(base)):
        s = float(np.sign(base[i]))
        if blocco and s != blocco:
            blocco = 0.0
        if s == 0.0 or s == blocco:
            verso, estremo = 0.0, np.nan
            continue
        if verso != s:                       # nuova entrata (o giro di posizione)
            verso, estremo = s, c[i]
        else:
            estremo = max(estremo, c[i]) if s > 0 else min(estremo, c[i])
        if np.isfinite(a[i]) and ((s > 0 and c[i] < estremo - k * a[i]) or (s < 0 and c[i] > estremo + k * a[i])):
            verso, estremo, blocco = 0.0, np.nan, s
            continue
        out[i] = base[i]
    return pd.DataFrame({m: out}, index=W.index)


# regole classiche con i parametri dei libri, tradotti in giorni di calendario (×7/5): sono scelte PRIMA
# di guardare i risultati, per non gonfiare i numeri del voto
def classiche(dati, m="ORO_EUR") -> dict:
    return {
        "media 280": ls_media(dati, m, n=280)[m],                       # media a 200 giorni di borsa
        "incrocio 70/280": ls_incrocio(dati, m, veloce=70, lenta=280)[m],  # incrocio 50/200 di borsa
        "Turtle 77/28": ls_donchian(dati, m, entrata=77, uscita=28)[m],    # Donchian 55/20 di borsa
        "momentum 12 mesi": ls_momentum(dati, m, orizzonti=(365,))[m],
        "canale gaussiano 144": ls_gaussiano(dati, m, periodo=144, molt=1.414)[m],
    }


def voto(dati, m="ORO_EUR", regole: dict | None = None) -> pd.DataFrame:
    """Voto di più regole di tendenza: peso = media dei loro voti (da -1 a +1, a gradini di 0,2)."""
    r = regole if regole is not None else classiche(dati, m)
    w = pd.concat(list(r.values()), axis=1).mean(axis=1)
    return pd.DataFrame({m: (w / 0.1).round() * 0.1}, index=w.index)


def nucleo_e_tattica(dati, m="ORO_EUR", nucleo=0.5, tattica: pd.DataFrame | None = None) -> pd.DataFrame:
    """Oro tenuto (nucleo) più una parte tattica che segue il voto di tendenza: peso = nucleo +
    (1 - nucleo) x voto. Con nucleo 0,3 si può arrivare a -0,4 (un po' short) solo quando tutte le regole
    dicono ribasso; con 0,5 si va da 0 a 1 (mai short)."""
    t = (tattica if tattica is not None else voto(dati, m))[m]
    return pd.DataFrame({m: (nucleo + (1.0 - nucleo) * t).round(2)}, index=t.index)


# --------------------------------------------------------------------------- ritorno alla media
def ls_rsi2(dati, m, n_media=200, basso=10, alto=90, uscita=50, lato="ls"):
    """RSI a 2 giorni (Connors) solo a favore di tendenza: con l'oro sopra la media a n giorni compra
    gli eccessi di ribasso (RSI sotto 'basso'); sotto la media vende allo scoperto gli eccessi di
    rialzo (RSI sopra 'alto'). Esce quando l'RSI torna oltre 'uscita'."""
    c = dati[m]["close"]
    r = rsi(c, 2)
    s = sma(c, n_media)
    su, giu = c > s, c < s
    w = macchina(su & (r < basso), (r > uscita) | giu, giu & (r > alto), (r < uscita) | su)
    return _out(m, w, c.index, lato)


def ls_bollinger(dati, m, n=20, k=2.0, lato="ls"):
    """Bande di Bollinger, ritorno alla media nei due sensi: compra sotto la banda bassa, vende allo
    scoperto sopra la banda alta; esce quando il prezzo torna alla media."""
    c = dati[m]["close"]
    mid = sma(c, n)
    sd = c.rolling(n, min_periods=n).std()
    w = macchina(c < mid - k * sd, c > mid, c > mid + k * sd, c < mid)
    return _out(m, w, c.index, lato)
