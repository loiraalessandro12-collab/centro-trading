"""Le ricette del bot: le STESSE regole, con gli stessi parametri, uscite dal torneo del 2026-11-03 e
dalle prove di miglioramento del 2026-11-04 (torneo/miglioramenti.py e miglioramenti2.py).

Ogni ricetta riceve i prezzi giornalieri {mercato: candele} e restituisce i pesi desiderati
(decisi alla chiusura di ogni giorno, eseguiti all'apertura del giorno dopo).

Cambio del 2026-11-04 (prima che il bot facesse il primo ordine): nei profili 2 e 3 l'oro non segue
più la media a 200 giorni ma è «a volatilità obiettivo» (peso = 10% / volatilità a 60 giorni, al massimo
100%): nei test ha dato più rendimento e meno cali in tutti i periodi.
"""
from __future__ import annotations

import pandas as pd

from torneo import strategie as ST

GAUSS = dict(periodo=144, molt=2.0)


def _oro_volatilita(dati: dict) -> pd.Series:
    return ST.solo_volatilita(dati, "ORO_EUR", vol_obiettivo=0.10, giorni_vol=60)["ORO_EUR"]


def tendenza_btc(dati: dict) -> pd.DataFrame:
    """Profilo 1 · aggressivo. Bitcoin quando il canale gaussiano (periodo 144, banda 2) dice
    'tendenza al rialzo', altrimenti tutto in euro."""
    return ST.canale_gaussiano(dati, "BTC_EUR", **GAUSS)


def tendenza_btc_conferma(dati: dict) -> pd.DataFrame:
    """Profilo 1+. Come il profilo 1, ma entra ed esce solo dopo 3 chiusure di fila oltre la banda:
    nei test ha ridotto il calo massimo in tutti e tre i periodi."""
    return ST.canale_gaussiano(dati, "BTC_EUR", **GAUSS, conferma=3)


def tendenza_btc_insieme(dati: dict) -> pd.DataFrame:
    """Profilo 1 · insieme. Media di 6 canali gaussiani vicini (periodo 100, 144, 200 × banda 1,414 e 2):
    peso = quota di canali «dentro», a gradini di 5%. Dai video della playlist «AI» (2026-11-04): calo
    massimo più basso in tutti i periodi (50% contro 77% nel 2013-2017, 32% contro 42% dal 2018)."""
    pezzi = [ST.canale_gaussiano(dati, "BTC_EUR", periodo=p, molt=k)["BTC_EUR"] for p in (100, 144, 200) for k in (1.414, 2.0)]
    w = pd.concat(pezzi, axis=1).mean(axis=1)
    return pd.DataFrame({"BTC_EUR": (w / 0.05).round() * 0.05})


def tendenza_btc_oro(dati: dict) -> pd.DataFrame:
    """Profilo 2 · dinamico. Come il profilo 1; quando bitcoin è fuori, i soldi vanno sull'oro (PAXG)
    con il peso della volatilità obiettivo (di solito 60-80%), il resto in euro."""
    wb = ST.canale_gaussiano(dati, "BTC_EUR", **GAUSS)["BTC_EUR"]
    wo = _oro_volatilita(dati).reindex(wb.index).fillna(0.0)
    return pd.DataFrame({"BTC_EUR": wb, "ORO_EUR": ((1 - wb) * wo).round(2)})


def meta_tendenza_meta_oro(dati: dict) -> pd.DataFrame:
    """Profilo 3 · bilanciato. Metà del capitale segue il profilo 1 su bitcoin, l'altra metà sta
    sull'oro a volatilità obiettivo. Nel 2018-2026: +26% l'anno, calo massimo 16%."""
    wb = ST.canale_gaussiano(dati, "BTC_EUR", **GAUSS)["BTC_EUR"] * 0.5
    wo = _oro_volatilita(dati) * 0.5
    return pd.concat([wb, wo], axis=1, sort=True).fillna(0.0)


def prudente(dati: dict) -> pd.DataFrame:
    """Profilo 4 · prudente. Metà con l'insieme di Donchian a volatilità obiettivo su bitcoin
    (Zarattini, Pagani e Barbon 2025), metà sull'oro a volatilità obiettivo. Nel 2018-2026:
    +11% l'anno, calo massimo 9%."""
    wb = ST.donchian_insieme(dati, "BTC_EUR")["BTC_EUR"] * 0.5
    wo = _oro_volatilita(dati) * 0.5
    return pd.concat([wb, wo], axis=1, sort=True).fillna(0.0)


def monete_forti(dati: dict) -> pd.DataFrame:
    """Profilo 5 · monete forti (torneo 2, 2026-11-04). Ogni domenica, fra le 20 monete più scambiate,
    compra le 5 più salite nelle ultime 2 settimane, a pesi uguali, solo se bitcoin è in tendenza (canale
    gaussiano acceso); altrimenti tutto in euro. Nei test: +55% l'anno dal 2018 con calo massimo 55%,
    anni da −36% a +944%. Rischio alto."""
    return ST.momentum_monete(dati, "BTC_EUR", K=5, L=14)


def btc_tenuto(dati: dict) -> pd.DataFrame:
    """Confronto: comprare bitcoin il primo giorno e tenerlo."""
    return ST.compra_e_tieni(dati, "BTC_EUR")


def meta_btc_meta_oro(dati: dict) -> pd.DataFrame:
    """Confronto: metà bitcoin e metà oro, tenuti (si ribilancia solo se ci si sposta di oltre 5 punti)."""
    idx = dati["BTC_EUR"].index.union(dati["ORO_EUR"].index)
    return pd.DataFrame({"BTC_EUR": 0.5, "ORO_EUR": 0.5}, index=idx)


RICETTE = {"tendenza_btc": tendenza_btc, "tendenza_btc_conferma": tendenza_btc_conferma, "tendenza_btc_insieme": tendenza_btc_insieme,
           "tendenza_btc_oro": tendenza_btc_oro, "meta_tendenza_meta_oro": meta_tendenza_meta_oro,
           "prudente": prudente, "monete_forti": monete_forti, "btc_tenuto": btc_tenuto, "meta_btc_meta_oro": meta_btc_meta_oro}
