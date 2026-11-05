"""Le strategie del torneo. Ognuna restituisce i PESI desiderati (vedi motore.py) decisi a fine
candela usando SOLO i prezzi fino a quella candela.

Da dove vengono (ricerca del 2026-11-03):
- dai video salvati: canale gaussiano (Michael Automates / Signum), RSI 30-70 con media a 200
  (Zinho Automates), incrocio EMA 9 / VWAP (reel di Tim Hutter), "presa delle liquidità"
  (reel ICT/TJR), breakout all'apertura (Steven Ace, Mamba FX), rete neurale (reel di J. Alampour,
  qui sostituita da un modello statistico onesto, rifatto ogni mese solo col passato);
- dalla letteratura: filtro della media a 200, doppio filtro (vecchio bot), incrocio di medie,
  Donchian (Turtle), momentum nel tempo su più orizzonti, rotazione fra mercati (dual momentum),
  momentum fra monete (cross-sectional), volatility targeting.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


# --------------------------------------------------------------------------- indicatori
def sma(x: pd.Series, n: int) -> pd.Series:
    return x.rolling(n, min_periods=n).mean()


def ema(x: pd.Series, n: int) -> pd.Series:
    return x.ewm(span=n, adjust=False, min_periods=n).mean()


def rsi(c: pd.Series, n: int = 14) -> pd.Series:
    d = c.diff()
    su, giu = d.clip(lower=0.0), (-d).clip(lower=0.0)
    a = 1.0 / n
    ms = su.ewm(alpha=a, adjust=False, min_periods=n).mean()
    mg = giu.ewm(alpha=a, adjust=False, min_periods=n).mean()
    rs = ms / mg.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    return out.where(~((mg == 0) & (ms > 0)), 100.0)


def vol_annua(c: pd.Series, span: int = 30, candele_anno: float = 365.0) -> pd.Series:
    r = np.log(c).diff()
    return np.sqrt((r ** 2).ewm(span=span, adjust=False, min_periods=20).mean() * candele_anno)


def stato(entra: np.ndarray, esci: np.ndarray) -> np.ndarray:
    """Da segnali di entrata/uscita a posizione 1/0 (si resta dentro finché non arriva l'uscita)."""
    s = pd.Series(np.where(entra, 1.0, np.where(esci, 0.0, np.nan)))
    return s.ffill().fillna(0.0).to_numpy(copy=True)


def filtro_gaussiano(x: np.ndarray, periodo: float, poli: int = 4) -> np.ndarray:
    """Filtro gaussiano di Ehlers a N poli (quello del 'Gaussian Channel' di DonovanWall)."""
    beta = (1 - math.cos(2 * math.pi / periodo)) / (1.414 ** (2.0 / poli) - 1)
    a = -beta + math.sqrt(beta * beta + 2 * beta)
    b = 1 - a
    coeff = [math.comb(poli, k) * (-1) ** (k + 1) * b ** k for k in range(1, poli + 1)]
    f = np.zeros(len(x))
    primo = np.flatnonzero(np.isfinite(x))
    if len(primo) == 0:
        return np.full(len(x), np.nan)
    p0 = primo[0]
    f[:p0] = np.nan
    f[p0:p0 + poli] = x[p0:p0 + poli]
    for i in range(p0 + poli, len(x)):
        v = a ** poli * x[i]
        for k in range(poli):
            v += coeff[k] * f[i - 1 - k]
        f[i] = v
    f[: p0 + int(periodo)] = np.nan     # riscaldamento
    return f


# --------------------------------------------------------------------------- strategie su un mercato
def _df(dati, m):
    return dati[m]


def compra_e_tieni(dati, m):
    c = _df(dati, m)["close"]
    return pd.DataFrame({m: np.where(c.notna(), 1.0, 0.0)}, index=c.index)


def filtro_media(dati, m, n=200):
    c = _df(dati, m)["close"]
    w = (c > sma(c, n)).astype(float)
    return pd.DataFrame({m: w.to_numpy()}, index=c.index)


def doppio_filtro(dati, m, n=200, lb=90):
    c = _df(dati, m)["close"]
    w = ((c > sma(c, n)) & (c / c.shift(lb) - 1 > 0)).astype(float)
    return pd.DataFrame({m: w.to_numpy()}, index=c.index)


def incrocio_medie(dati, m, veloce=20, lenta=100):
    c = _df(dati, m)["close"]
    w = (ema(c, veloce) > ema(c, lenta)).astype(float)
    return pd.DataFrame({m: w.to_numpy()}, index=c.index)


def donchian(dati, m, entrata=55, uscita=20):
    d = _df(dati, m)
    c = d["close"]
    alto = d["high"].rolling(entrata, min_periods=entrata).max().shift(1)
    basso = d["low"].rolling(uscita, min_periods=uscita).min().shift(1)
    w = stato((c > alto).to_numpy(), (c < basso).to_numpy())
    return pd.DataFrame({m: w}, index=c.index)


def momentum_orizzonti(dati, m, orizzonti=(30, 60, 90, 180, 365)):
    """Momentum nel tempo su più orizzonti: peso = quota di orizzonti con rendimento positivo
    (0, 0,2, 0,4 ... 1). È la versione 'ensemble' usata da molti fondi trend following."""
    c = _df(dati, m)["close"]
    voti = [((c / c.shift(L) - 1) > 0).astype(float).where(c.shift(L).notna()) for L in orizzonti]
    w = pd.concat(voti, axis=1).mean(axis=1, skipna=False).fillna(0.0)
    return pd.DataFrame({m: w.to_numpy()}, index=c.index)


def _di_fila(x: np.ndarray, k: int) -> np.ndarray:
    """Vero solo se la condizione è vera nelle ultime k candele di fila (k=1: com'è)."""
    if k <= 1:
        return x
    s = pd.Series(x.astype(float))
    return (s.rolling(k, min_periods=k).sum() >= k).to_numpy()


def canale_gaussiano(dati, m, periodo=144, molt=1.414, uscita="banda", conferma=1, sorgente="hlc3", livelli=False):
    """Canale gaussiano: entra se il filtro sale e la chiusura supera la banda alta; esce sotto la
    banda alta (o sotto il filtro, se uscita='filtro'). Con conferma=k entrata e uscita aspettano
    k chiusure di fila (meno falsi segnali, ma si entra e si esce più tardi)."""
    d = _df(dati, m)
    src = (((d["high"] + d["low"] + d["close"]) / 3) if sorgente == "hlc3" else ((d["high"] + d["low"]) / 2)).to_numpy(dtype=float)
    c_prec = d["close"].shift(1)
    tr = pd.concat([d["high"] - d["low"], (d["high"] - c_prec).abs(), (d["low"] - c_prec).abs()],
                   axis=1).max(axis=1).to_numpy(dtype=float)
    f = filtro_gaussiano(src, periodo)
    ftr = filtro_gaussiano(tr, periodo)
    alta = f + ftr * molt
    c = d["close"].to_numpy(dtype=float)
    sale = np.concatenate([[False], f[1:] > f[:-1]])
    with np.errstate(invalid="ignore"):
        entra = sale & (c > alta)
        esci = (c < alta) if uscita == "banda" else (c < f)
    entra, esci = _di_fila(entra, conferma), _di_fila(esci, conferma)
    w = stato(entra, esci)
    w[~np.isfinite(f)] = 0.0
    if livelli:   # per il cruscotto: filtro, banda alta e se il filtro sale, candela per candela
        return pd.DataFrame({m: w, "filtro": f, "banda_alta": alta, "sale": sale.astype(float)}, index=d.index)
    return pd.DataFrame({m: w}, index=d.index)


def rsi_media200(dati, m, n_rsi=14, basso=30, alto=70, n_media=200):
    """Dal video Zinho: compra quando l'RSI risale sopra 30 col prezzo sopra la media a 200;
    vende quando l'RSI supera 70 oppure il prezzo scende sotto la media a 200."""
    c = _df(dati, m)["close"]
    r = rsi(c, n_rsi)
    med = ema(c, n_media)
    entra = ((r > basso) & (r.shift(1) <= basso) & (c > med)).to_numpy()
    esci = (((r > alto) & (r.shift(1) <= alto)) | (c < med)).to_numpy()
    return pd.DataFrame({m: stato(entra, esci)}, index=c.index)


def presa_liquidita(dati, m, giorni=3):
    """'Liquidity sweep' (ICT/TJR) in versione giornaliera: oggi il prezzo scende sotto il minimo di
    ieri (fa scattare gli stop) ma chiude sopra quel minimo -> compra; tiene 'giorni' candele o
    esce se chiude sotto il minimo della candela della 'presa'."""
    d = _df(dati, m)
    lo_prec = d["low"].shift(1)
    segnale = ((d["low"] < lo_prec) & (d["close"] > lo_prec)).to_numpy()
    c = d["close"].to_numpy()
    lo = d["low"].to_numpy()
    w = np.zeros(len(d))
    tenuta, stop = 0, np.nan
    for i in range(len(d)):
        if tenuta > 0:
            if c[i] < stop:
                tenuta = 0
            else:
                tenuta -= 1
                w[i] = 1.0 if tenuta > 0 else 0.0
                continue
        if segnale[i]:
            tenuta, stop = giorni, lo[i]
            w[i] = 1.0
    return pd.DataFrame({m: w}, index=d.index)


# --------------------------------------------------------------------------- portafogli di più mercati
def _calendario(dati, mercati):
    idx = None
    for m in mercati:
        idx = dati[m].index if idx is None else idx.union(dati[m].index)
    return idx


def _ribilancia(pesi: pd.DataFrame, ogni: str) -> pd.DataFrame:
    """Tiene i pesi solo nei giorni di ribilanciamento ('W' = domenica, 'M' = fine mese)."""
    if ogni == "D":
        return pesi
    giorni = pesi.index.to_series()
    if ogni == "W":
        scelta = (giorni.dt.dayofweek == 6).to_numpy()
    else:
        scelta = (giorni.dt.month != giorni.shift(-1).dt.month).to_numpy()
    out = pesi.copy()
    out.loc[~scelta] = np.nan
    return out.ffill().fillna(0.0)


def rotazione(dati, mercati, lookback=90, migliori=1, ogni="W", filtro_assoluto=True):
    """Dual momentum: ogni settimana (o mese) compra i 'migliori' mercati per rendimento degli ultimi
    'lookback' giorni, solo se quel rendimento è positivo; altrimenti resta in contanti."""
    idx = _calendario(dati, mercati)
    C = pd.DataFrame({m: dati[m]["close"].reindex(idx) for m in mercati})
    fresco = pd.DataFrame({m: dati[m]["close"].reindex(idx).notna() for m in mercati})
    Cf = C.ffill()
    mom = Cf / Cf.shift(lookback) - 1
    valido = fresco & Cf.shift(lookback).notna() & (fresco.astype(float).rolling(lookback, min_periods=1).mean() > 0.9)
    mom = mom.where(valido)
    rango = mom.rank(axis=1, ascending=False)
    scelti = (rango <= migliori)
    if filtro_assoluto:
        scelti &= mom > 0
    pesi = scelti.astype(float) / migliori
    return _ribilancia(pesi, ogni)


def momentum_fra_monete(dati, mercati, lookback=30, migliori=3, ogni="W", regime="BTC_EUR", n_regime=200):
    """Momentum fra monete (cross-sectional): ogni settimana compra le 'migliori' monete per
    rendimento recente, a pesi uguali, solo se bitcoin è sopra la sua media a 200 giorni.
    ATTENZIONE: l'elenco delle monete è quello di oggi (chi è sparito non c'è): i risultati
    sono ottimisti ('pregiudizio del sopravvissuto')."""
    pesi = rotazione(dati, mercati, lookback=lookback, migliori=migliori, ogni="D", filtro_assoluto=True)
    if regime:
        c = dati[regime]["close"].reindex(pesi.index).ffill()
        ok = (c > sma(c, n_regime)).to_numpy()
        pesi = pesi.mul(ok.astype(float), axis=0)
    return _ribilancia(pesi, ogni)


def con_volatilita(pesi: pd.DataFrame, dati, obiettivo=0.5, tetto=1.0, span=30) -> pd.DataFrame:
    """Volatility targeting: riduce il peso quando il mercato è agitato
    (peso x min(tetto, obiettivo / volatilità stimata)). Con 'tetto' 1 non c'è mai leva."""
    out = pesi.copy()
    for m in pesi.columns:
        v = vol_annua(dati[m]["close"].reindex(pesi.index).ffill(), span)
        f = np.minimum(tetto, obiettivo / v.replace(0, np.nan)).fillna(0.0)
        out[m] = ((pesi[m] * f) / 0.1).round() * 0.1      # gradini da 0,1: meno micro-ordini
    return out


# --------------------------------------------------------------------------- "AI": modello statistico rifatto ogni mese
def _caratteristiche(c: pd.Series) -> pd.DataFrame:
    lr = np.log(c)
    X = pd.DataFrame(index=c.index)
    for L in (1, 5, 20, 60, 120):
        X[f"r{L}"] = lr.diff(L)
    X["vol20"] = lr.diff().rolling(20).std()
    X["d50"] = lr - np.log(sma(c, 50))
    X["d200"] = lr - np.log(sma(c, 200))
    X["rsi14"] = rsi(c, 14) / 100 - 0.5
    return X


def _logistica(X: np.ndarray, y: np.ndarray, l2: float = 1.0, iter_: int = 25) -> np.ndarray:
    """Regressione logistica con penalità L2, risolta col metodo di Newton (IRLS)."""
    Xb = np.hstack([np.ones((len(X), 1)), X])
    w = np.zeros(Xb.shape[1])
    R = l2 * np.eye(Xb.shape[1])
    R[0, 0] = 0.0
    for _ in range(iter_):
        p = 1 / (1 + np.exp(-np.clip(Xb @ w, -30, 30)))
        g = Xb.T @ (p - y) + R @ w
        H = (Xb * (p * (1 - p))[:, None]).T @ Xb + R
        try:
            passo = np.linalg.solve(H, g)
        except np.linalg.LinAlgError:
            break
        w -= passo
        if np.max(np.abs(passo)) < 1e-6:
            break
    return w


def modello_ai(dati, m, orizzonte=5, anni_addestramento=3, margine=0.03):
    """Strategia 'AI' onesta: ogni inizio mese si allena una regressione logistica SOLO sui 3 anni
    precedenti (escluse le ultime 'orizzonte' candele, il cui risultato non si conosce ancora) per
    stimare la probabilità che il prezzo salga nei prossimi 'orizzonte' giorni; si compra se la
    probabilità supera 0,5 + margine."""
    c = dati[m]["close"].dropna()
    X = _caratteristiche(c)
    y = (c.shift(-orizzonte) / c - 1 > 0).astype(float)
    prob = pd.Series(np.nan, index=c.index)
    mesi = c.index.to_series().dt.to_period("M")
    giorni_add = int(365 * anni_addestramento)
    for mese in mesi.unique():
        nel_mese = c.index[(mesi == mese).to_numpy()]
        t0 = nel_mese[0]
        pos0 = c.index.get_loc(t0)
        fine_add = pos0 - orizzonte          # l'etichetta dell'ultima riga usata è già nota a t0
        ini_add = max(0, fine_add - giorni_add)
        if fine_add - ini_add < 500:
            continue
        Xa = X.iloc[ini_add:fine_add]
        ya = y.iloc[ini_add:fine_add]
        ok = Xa.notna().all(axis=1) & ya.notna()
        Xa, ya = Xa[ok].to_numpy(), ya[ok].to_numpy()
        if len(Xa) < 400:
            continue
        mu, sd = Xa.mean(axis=0), Xa.std(axis=0) + 1e-12
        w = _logistica((Xa - mu) / sd, ya)
        Xp = X.loc[nel_mese]
        okp = Xp.notna().all(axis=1).to_numpy()
        z = np.hstack([np.ones((len(Xp), 1)), ((Xp.to_numpy() - mu) / sd)]) @ w
        p = 1 / (1 + np.exp(-np.clip(z, -30, 30)))
        prob.loc[nel_mese] = np.where(okp, p, np.nan)
    w = (prob > 0.5 + margine).astype(float)
    out = pd.DataFrame({m: w.to_numpy()}, index=c.index)
    return out.reindex(dati[m].index).fillna(0.0)


# --------------------------------------------------------------------------- strategie intraday (candele orarie)
def ema_vwap(dati, m, n_ema=9, inizio_sessione_utc=0, solo_long=True):
    """Dal reel 'EMA 9 + VWAP': long quando la EMA a 9 candele passa sopra il VWAP della sessione
    (prezzo medio pesato per i volumi, azzerato all'inizio di ogni giornata), fuori o short quando
    ripassa sotto."""
    d = _df(dati, m)
    tipico = (d["high"] + d["low"] + d["close"]) / 3
    vol = d["volume"].replace(0, np.nan).fillna(1.0)
    sessione = (d.index - pd.Timedelta(hours=inizio_sessione_utc)).normalize()
    pv = (tipico * vol).groupby(sessione).cumsum()
    vv = vol.groupby(sessione).cumsum()
    vwap = pv / vv
    e = ema(d["close"], n_ema)
    su = (e > vwap).to_numpy()
    w = np.where(su, 1.0, 0.0 if solo_long else -1.0)
    w[~np.isfinite(e.to_numpy())] = 0.0
    return pd.DataFrame({m: w}, index=d.index)


def breakout_apertura_ny(dati, m, ore_range=1, ultima_ora_entrata=3, solo_long=False, stop_al_range=True):
    """Breakout dell'apertura di New York (dai video di Steven Ace e Mamba FX), su candele orarie:
    il 'range' è la prima ora dalle 9 alle 10 di New York; se una candela successiva chiude sopra il
    massimo del range si compra (sotto il minimo si vende), entro 'ultima_ora_entrata' ore; si chiude
    tutto alle 16 di New York, oppure se il prezzo torna oltre il lato opposto del range."""
    d = _df(dati, m)
    ny = d.index.tz_localize("UTC").tz_convert("America/New_York")
    ora = ny.hour.to_numpy()
    giorno = ny.normalize()
    w = np.zeros(len(d))
    hi = d["high"].to_numpy()
    lo = d["low"].to_numpy()
    c = d["close"].to_numpy()
    feriale = ny.dayofweek.to_numpy() < 5
    i = 0
    n = len(d)
    gg = giorno.to_numpy()
    while i < n:
        # cerca l'inizio del range (candela che apre alle 9 di New York)
        if not (feriale[i] and ora[i] == 9):
            i += 1
            continue
        g = gg[i]
        fine_range = i + ore_range
        if fine_range > n or any(gg[j] != g for j in range(i, min(fine_range, n))):
            i += 1
            continue
        r_hi, r_lo = hi[i:fine_range].max(), lo[i:fine_range].min()
        pos = 0.0
        j = fine_range
        while j < n and gg[j] == g and ora[j] < 16:
            if pos == 0.0 and ora[j] < 9 + ore_range + ultima_ora_entrata:
                if c[j] > r_hi:
                    pos = 1.0
                elif c[j] < r_lo and not solo_long:
                    pos = -1.0
            elif pos != 0.0 and stop_al_range:
                if (pos > 0 and c[j] < r_lo) or (pos < 0 and c[j] > r_hi):
                    pos = 0.0
            w[j] = pos if ora[j] < 15 else 0.0     # la decisione alla chiusura delle 15-16 = chiudere
            j += 1
        i = j
    return pd.DataFrame({m: w}, index=d.index)


# --------------------------------------------------------------------------- dalla ricerca del 2026-11-04
def donchian_insieme(dati, m, finestre=(20, 30, 60, 90, 150, 250, 360), vol_obiettivo=0.25, giorni_vol=90, tetto=1.0):
    """Insieme di Donchian con volatilità obiettivo (Zarattini, Pagani e Barbon, 2025): per ogni finestra n
    si entra quando la chiusura fa il massimo degli ultimi n giorni; lo stop parte dal centro del canale e
    sale col centro (mai giù); si esce se la chiusura scende sotto lo stop. Ogni sotto-modello pesa
    min(vol_obiettivo / volatilità a 90 giorni, tetto); il peso finale è la media dei sotto-modelli."""
    c = dati[m]["close"]
    cc = c.to_numpy(dtype=float)
    r = np.log(c).diff()
    sigma = (r.rolling(giorni_vol, min_periods=giorni_vol // 2).std() * math.sqrt(365)).to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        peso_sotto = np.where(np.isfinite(sigma) & (sigma > 0), np.minimum(vol_obiettivo / sigma, tetto), 0.0)
    totale = np.zeros(len(cc))
    for n in finestre:
        alto = c.rolling(n, min_periods=n).max().to_numpy()
        basso = c.rolling(n, min_periods=n).min().to_numpy()
        centro = (alto + basso) / 2
        dentro, stop = False, np.nan
        pos = np.zeros(len(cc))
        for i in range(len(cc)):
            if not np.isfinite(alto[i]):
                continue
            if not dentro:
                if cc[i] >= alto[i]:
                    dentro, stop = True, centro[i]
            else:
                stop = max(stop, centro[i])
                if cc[i] <= stop:
                    dentro = False
            pos[i] = 1.0 if dentro else 0.0
        totale += pos * peso_sotto
    w = totale / len(finestre)
    return pd.DataFrame({m: (w / 0.05).round() * 0.05}, index=c.index)


def solo_volatilita(dati, m, vol_obiettivo=0.10, giorni_vol=60, tetto=1.0):
    """Nessun segnale: si tiene sempre il mercato, con un peso = min(vol_obiettivo / volatilità, tetto)."""
    c = dati[m]["close"]
    sigma = np.log(c).diff().rolling(giorni_vol, min_periods=giorni_vol // 2).std() * math.sqrt(365)
    w = np.minimum(vol_obiettivo / sigma.replace(0, np.nan), tetto).fillna(0.0)
    return pd.DataFrame({m: ((w / 0.05).round() * 0.05).to_numpy()}, index=c.index)


def momentum_monete(dati, chiave_btc="BTC_EUR", K=5, L=14, n_universo=20, storia=120):
    """Momentum fra monete con bitcoin in tendenza (torneo 2, 2026-11-04): ogni domenica, fra le n_universo
    monete più scambiate negli ultimi 30 giorni (in euro: volume x prezzo) con almeno 'storia' giorni di prezzi,
    compra le K più salite negli ultimi L giorni (solo se salite), a pesi uguali; tutto in euro se il canale
    gaussiano su bitcoin è spento. Usa solo dati fino al giorno della decisione."""
    chiavi = list(dati)
    idx = None
    for m in chiavi:
        idx = dati[m].index if idx is None else idx.union(dati[m].index)
    C = pd.DataFrame({m: dati[m]["close"].reindex(idx) for m in chiavi})
    if all("qv" in dati[m].columns for m in chiavi):
        V = pd.DataFrame({m: dati[m]["qv"].reindex(idx) for m in chiavi})
    else:
        V = pd.DataFrame({m: (dati[m]["volume"] * dati[m]["close"]).reindex(idx) for m in chiavi})
    vol30 = V.rolling(30, min_periods=20).mean()
    eta = C.notna().cumsum()
    ok = C.notna() & (eta >= storia) & vol30.notna()
    U = (vol30.where(ok).rank(axis=1, ascending=False) <= n_universo) & ok
    reg = canale_gaussiano({chiave_btc: dati[chiave_btc]}, chiave_btc, periodo=144, molt=2.0)[chiave_btc].reindex(idx).ffill().fillna(0.0) > 0
    mom = C / C.shift(L) - 1
    W = pd.DataFrame(0.0, index=idx, columns=chiavi)
    for t in idx[idx.dayofweek == 6]:
        if not reg.loc[t]:
            continue
        m = mom.loc[t].where(U.loc[t]).dropna()
        m = m[m > 0].sort_values(ascending=False).head(K)
        if len(m):
            W.loc[t, m.index] = 1.0 / K
    return _ribilancia(W, "W")
