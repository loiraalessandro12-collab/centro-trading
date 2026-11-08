"""L'ARENA del centro di controllo: tutte le strategie girano insieme, con soldi finti, sugli stessi
prezzi pubblici di Kraken. Stesse regole del torneo: decisione alla chiusura della candela,
esecuzione all'apertura della successiva, commissioni e slittamento, ordine minimo 5 euro.

Due cose diverse, da non confondere:
- STORICO: la strategia provata sul passato (dal 2018, o dal 2021 per quelle a 1 ora);
- DAL VIVO: la strategia che gira da oggi in avanti, sui prezzi veri di ogni minuto.
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
import pandas as pd

QUI = os.path.dirname(os.path.abspath(__file__))
RADICE = os.path.dirname(QUI)
sys.path.insert(0, RADICE)

from bot_definitivo import ricette as R  # noqa: E402
from torneo import strategie as ST  # noqa: E402
from torneo import strategie_oro as SO  # noqa: E402
from torneo.motore import Costi, simula  # noqa: E402

MERCATI = {"BTC_EUR": "BTC/EUR", "ETH_EUR": "ETH/EUR", "SOL_EUR": "SOL/EUR", "ORO_EUR": "PAXG/EUR"}
NOMI_MERCATI = {"BTC_EUR": "Bitcoin", "ETH_EUR": "Ether", "SOL_EUR": "Solana", "ORO_EUR": "Oro (PAXG)"}
# monete per il profilo 5 (solo candele giornaliere; il server tiene quelle che Kraken quota in euro)
ALT = ["BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "DOT", "LTC", "LINK", "AVAX", "ATOM", "XLM", "ETC", "UNI", "FIL",
       "ALGO", "AAVE", "NEAR", "ICP", "TRX", "SHIB", "PEPE", "SUI", "APT", "ARB", "OP", "INJ", "SEI", "RENDER", "IMX",
       "GRT", "SAND", "MANA", "AXS", "CRV", "SNX", "LDO", "FET", "TIA", "WIF", "BONK", "HBAR", "XTZ", "EOS", "BCH",
       "POL", "ENA", "ONDO", "TAO", "JUP"]
MERCATI_ALT = {f"{m}_EUR": f"{m}/EUR" for m in ALT}
COMMISSIONE = 0.004
SLITTAMENTO = {"BTC_EUR": 0.0005, "ETH_EUR": 0.0005, "SOL_EUR": 0.001, "ORO_EUR": 0.0015}


# oro con un CFD (long e short): spread e slittamento 0,012% per lato; finanziamento notturno long 6% e
# short 1% all'anno (torneo dell'oro del 2026-11-06, ricerca/oro_mercato_2026-11.md)
COSTI_CFD_ORO = {"ORO_EUR": Costi(commissione=0.0, slittamento=0.00012, fin_long_annuo=0.06, fin_short_annuo=0.01)}


def costi(mercati) -> dict:
    return {m: Costi(commissione=COMMISSIONE, slittamento=SLITTAMENTO.get(m, 0.001), minimo_ordine_eur=5.0)
            for m in mercati}


def _mix(btc: pd.Series, oro: pd.Series) -> pd.DataFrame:
    """Due pezzi su calendari che possono differire di qualche giorno: li mette sulle stesse date."""
    idx = btc.index.union(oro.index)
    return pd.DataFrame({"BTC_EUR": btc.reindex(idx).ffill().fillna(0.0), "ORO_EUR": oro.reindex(idx).ffill().fillna(0.0)})


def candele_liquidita(tassi: list, fine: pd.Timestamp | None = None) -> pd.DataFrame:
    """La liquidità remunerata come «mercato»: un prezzo che cresce ogni giorno dell'€STR/365, mai sotto zero
    (un conto in euro non applica tassi negativi). tassi = [[aaaa-mm-gg, % annuo], ...] solo nei giorni di cambio."""
    s = pd.Series({pd.Timestamp(d): float(v) for d, v in tassi}).sort_index()
    fine = fine if fine is not None else pd.Timestamp.now("UTC").tz_localize(None).normalize()
    giorni = pd.date_range(s.index[0], fine, freq="D")
    t = s.reindex(s.index.union(giorni)).ffill().reindex(giorni).clip(lower=0.0) / 100.0 / 365.0
    c = 100.0 * (1.0 + t).cumprod()
    o = c.shift(1).fillna(100.0)
    return pd.DataFrame({"open": o, "high": c, "low": o, "close": c, "volume": 0.0})


def _diluito(W: pd.DataFrame, k: float) -> pd.DataFrame:
    """Una quota k del capitale segue la strategia, il resto è liquidità all'€STR."""
    out = (W.fillna(0.0) * k).round(3)
    out["LIQ"] = 1.0 - k
    return out


def _s(id_, nome, gruppo, tf, mercati, f, origine, descrizione):
    return {"id": id_, "nome": nome, "gruppo": gruppo, "tf": tf, "mercati": mercati, "f": f,
            "origine": origine, "descrizione": descrizione}


STRATEGIE = [
    _s("p1", "Profilo 1 · tendenza su bitcoin", "profilo", "1d", ["BTC_EUR"], R.tendenza_btc,
       "Torneo: canale gaussiano dal video di Michael Automates (Signum)",
       "Bitcoin quando il canale gaussiano (144, banda 2) è in tendenza al rialzo, altrimenti euro."),
    _s("p1c", "Profilo 1+ · con conferma di 3 chiusure", "profilo", "1d", ["BTC_EUR"], R.tendenza_btc_conferma,
       "Miglioria del 2026-11-04", "Come il profilo 1, ma entra ed esce solo dopo 3 chiusure di fila."),
    _s("p1i", "Profilo 1 · insieme di 6 canali", "profilo", "1d", ["BTC_EUR"], R.tendenza_btc_insieme,
       "Video della playlist «AI» (2026-11-04)", "Media di 6 canali gaussiani vicini: meno cali nei test, in prova per confermarlo."),
    _s("p2", "Profilo 2 · dinamico, oro di riserva", "profilo", "1d", ["BTC_EUR", "ORO_EUR"], R.tendenza_btc_oro,
       "Torneo + ricerca del 2026-11-04", "Come il profilo 1; quando bitcoin è fuori, oro dosato sulla sua oscillazione (di solito 60-80%)."),
    _s("p3", "Profilo 3 · bilanciato, metà e metà", "profilo", "1d", ["BTC_EUR", "ORO_EUR"], R.meta_tendenza_meta_oro,
       "Miglioria del 2026-11-04", "Metà segue il profilo 1 su bitcoin, metà sta sull'oro dosato sulla sua oscillazione."),
    _s("p4", "Profilo 4 · prudente", "profilo", "1d", ["BTC_EUR", "ORO_EUR"], R.prudente,
       "Ricerca: Zarattini, Pagani e Barbon (2025)", "Metà con 7 canali di Donchian a volatilità obiettivo su bitcoin, metà sull'oro dosato."),
    _s("sma200", "Media a 200 giorni su bitcoin", "letteratura", "1d", ["BTC_EUR"],
       lambda d: ST.filtro_media(d, "BTC_EUR", n=200), "Letteratura (trend following)",
       "Bitcoin sopra la sua media a 200 giorni, altrimenti euro."),
    _s("vecchio", "Doppio filtro (il vecchio bot)", "letteratura", "1d", ["BTC_EUR"],
       lambda d: ST.doppio_filtro(d, "BTC_EUR", n=200, lb=90), "TRADING BOT di giugno",
       "Bitcoin se sopra la media a 200 giorni e più alto di 90 giorni fa."),
    # 2026-11-08, decisione di Alessandro («vogliamo solo il meglio delle più redditizie»): tolte dalla gara le 4
    # strategie dei video che perdono anche sugli anni passati (rsi −5%, liquidita −29%, emavwap −43%, orb −43% l'anno
    # dal 2018) e le 2 deboli sull'oro long e short (oro_voto +4,8%, oro_completa +3,9%). Il codice resta in
    # torneo/strategie.py e torneo/strategie_oro.py.
    _s("p5", "Profilo 5 · monete forti", "profilo", "1d", list(MERCATI_ALT), R.monete_forti,
       "Torneo 2 (2026-11-04)", "Ogni domenica le 5 monete più salite in 2 settimane, solo con bitcoin in tendenza. Rischio alto."),
    {**_s("p6", "Profilo 6 · insieme di canali con leva 1,5", "profilo", "1d", ["BTC_EUR"],
          lambda d: R.tendenza_btc_insieme(d) * 1.5, "Prova sulla leva (2026-11-04)",
          "Come l'insieme di 6 canali ma con leva 1,5 (contratti a leva: commissione 0,05%, finanziamento 11,6% l'anno)."),
     "costi": {"BTC_EUR": Costi(commissione=0.0005, slittamento=0.0005, fin_long_annuo=0.116)}},
    _s("btc", "Bitcoin comprato e tenuto", "confronto", "1d", ["BTC_EUR"], R.btc_tenuto, "Confronto",
       "Si compra il primo giorno e si tiene."),
    _s("mista", "Metà bitcoin e metà oro, tenuti", "confronto", "1d", ["BTC_EUR", "ORO_EUR"], R.meta_btc_meta_oro,
       "Confronto", "Metà e metà, si ribilancia solo oltre 5 punti."),
    # --- tornei dell'oro (2026-11-06 e 2026-11-07): regole fissate con dati fino al 30/10/2026, prima della
    # partenza, così i 90 giorni della prova dal vivo restano sconosciuti a tutte
    _s("oro", "Oro comprato e tenuto", "confronto", "1d", ["ORO_EUR"], lambda d: ST.compra_e_tieni(d, "ORO_EUR"),
       "Confronto", "Si compra il token PAXG il primo giorno e si tiene. Nei tornei dell'oro ha battuto tutte le regole sul guadagno."),
    _s("oro_dosato", "Oro dosato sull'oscillazione", "oro", "1d", ["ORO_EUR"],
       lambda d: ST.solo_volatilita(d, "ORO_EUR", vol_obiettivo=0.10, giorni_vol=60), "Torneo dell'oro 2 (2026-11-07)",
       "Sempre long sull'oro (PAXG), con meno soldi quando l'oro è agitato: punta a un'oscillazione del 10% "
       "all'anno. Nel torneo il miglior rapporto fra guadagno e rischio solo sull'oro."),
    _s("oro_nucleo", "Oro 70% tenuto + 30% tattica", "oro", "1d", ["ORO_EUR"],
       lambda d: SO.nucleo_e_tattica(d, "ORO_EUR", nucleo=0.7), "Torneo dell'oro 2 (2026-11-07)",
       "Il 70% sta sempre sull'oro (PAXG); il resto segue il voto di 5 regole di tendenza classiche. "
       "Va da 40% a 100% d'oro: mai short."),
    _s("mix_oro", "Metà tendenza su bitcoin + metà oro con tattica", "mix", "1d", ["BTC_EUR", "ORO_EUR"],
       lambda d: _mix(0.5 * R.tendenza_btc(d)["BTC_EUR"], 0.5 * SO.nucleo_e_tattica(d, "ORO_EUR", nucleo=0.5)["ORO_EUR"]),
       "Torneo dell'oro 2 (2026-11-07)",
       "Metà del capitale segue il profilo 1 su bitcoin; l'altra metà sta sull'oro (PAXG), dal 50 al 100% secondo "
       "il voto di 5 regole. Nel torneo +25% all'anno dal 2018 con calo massimo del 16%."),
    {**_s("mix_voto", "Metà tendenza su bitcoin + metà oro long e short", "mix", "1d", ["BTC_EUR", "ORO_EUR"],
          lambda d: _mix(0.5 * R.tendenza_btc(d)["BTC_EUR"], 0.5 * SO.voto(d, "ORO_EUR")["ORO_EUR"]),
          "Torneo dell'oro 2 (2026-11-07)",
          "Metà segue il profilo 1 su bitcoin; l'altra metà va long o short sull'oro con il voto di 5 regole (CFD). "
          "Serve a vedere dal vivo se lo short sull'oro aiuta dentro una miscela."),
     "costi": {**costi(["BTC_EUR"]), **COSTI_CFD_ORO}},
    # --- torneo dei mesi (2026-11-08, torneo/mesi.py + ricerca/mesi_positivi_2026-11.md): richiesta di Alessandro,
    # «guadagni mese su mese con un basso rischio di perdite». Regole fissate con dati fino al 30/10/2026.
    {**_s("p3_30", "Profilo 3 al 30% + liquidità all'€STR", "mesi", "1d", ["BTC_EUR", "ORO_EUR", "LIQ"],
          lambda d: _diluito(R.meta_tendenza_meta_oro(d), 0.30), "Torneo dei mesi e ricerca (2026-11-08)",
          "Il 30% del capitale segue il Profilo 3; il 70% è liquidità che matura l'€STR (oggi circa 2,4% l'anno), "
          "per esempio su un conto remunerato. Nel torneo 2020-2026: +9,5% l'anno, 66% di mesi positivi, mese "
          "peggiore −1,7%, calo massimo 4%."),
     "costi": {**costi(["BTC_EUR", "ORO_EUR"]), "LIQ": Costi(commissione=0.0, slittamento=0.0)}},
    {**_s("p3_neutra", "Profilo 3 con zona neutra", "mesi", "1d", ["BTC_EUR", "ORO_EUR"], R.meta_tendenza_meta_oro,
          "Torneo dei mesi (2026-11-08); idea della «zona neutra» dal reel di joshuaaalampour",
          "Come il Profilo 3, ma ribilancia solo quando un peso si sposta di oltre 20 punti: meno ordini piccoli e meno "
          "commissioni. Nel torneo 2020-2026: +29,1% l'anno contro +26,1% del Profilo 3, anche con costi doppi."),
     "soglia": 0.20},
]
PER_ID = {s["id"]: s for s in STRATEGIE}


def _tempo(t) -> int:
    return int(pd.Timestamp(t).timestamp())


def _metriche(eq: pd.Series, anni_per_candela: float) -> dict:
    eq = eq.dropna()
    if len(eq) < 2:
        return {}
    gg = eq.groupby(eq.index.normalize()).last()
    r = gg.pct_change().dropna()
    anni = max((gg.index[-1] - gg.index[0]).days / 365.25, 1e-9)
    sd = r.std()
    return {"rendimento": float(eq.iloc[-1] / eq.iloc[0] - 1),
            "rendimento_annuo": float((eq.iloc[-1] / eq.iloc[0]) ** (1 / anni) - 1) if anni > 0.2 else None,
            "calo_massimo": float(-(eq / eq.cummax() - 1).min()),
            "sharpe": float(r.mean() / sd * math.sqrt(365)) if sd > 0 else 0.0}


def _operazioni(reg: list) -> dict:
    """Riassume gli ordini in 'giri' chiusi (da entrata a uscita) per mercato, long e short: quanti, quanti
    vinti. Uno short guadagna se il prezzo d'uscita è più basso di quello d'entrata."""
    giri, aperti = [], {}
    for o in reg:
        m, prima, dopo = o["mercato"], o["peso_prima"], o["peso_dopo"]
        gira = abs(prima) > 1e-9 and abs(dopo) > 1e-9 and (prima > 0) != (dopo > 0)
        if m in aperti and (abs(dopo) <= 1e-9 or gira):
            e = aperti.pop(m)
            verso = 1.0 if e["peso_dopo"] > 0 else -1.0
            giri.append(verso * (o["prezzo"] / e["prezzo"] - 1))
        if abs(dopo) > 1e-9 and (abs(prima) <= 1e-9 or gira):
            aperti[m] = o
    vinti = [g for g in giri if g > 0]
    return {"giri_chiusi": len(giri), "quota_vinti": (len(vinti) / len(giri)) if giri else None}


def esegui(s: dict, chiusi: dict, correnti: dict, partenza: pd.Timestamp, capitale: float) -> dict:
    """Una strategia dal vivo: pesi decisi solo sulle candele chiuse; simulazione dalla candela prima
    della partenza fino alla candela in corso, segnata al prezzo di adesso."""
    tf = s["tf"]
    dati = {m: chiusi[tf][m] for m in s["mercati"]}
    W = s["f"](dati).fillna(0.0)
    for m in s["mercati"]:
        if m not in W.columns:
            W[m] = 0.0
    W = W[s["mercati"]]
    base = s["mercati"][0]
    idx = dati[base].index
    con_ora = {}
    for m in s["mercati"]:
        df = dati[m].reindex(idx).ffill()
        c = correnti.get(tf, {}).get(m)
        if c is not None and (len(df) == 0 or c["tempo"] > df.index[-1]):
            df.loc[c["tempo"]] = [c["open"], c["high"], c["low"], c["close"], c["volume"]]
        con_ora[m] = df
    tutti = con_ora[base].index
    W = W.reindex(tutti).ffill().fillna(0.0)
    passo = pd.Timedelta(days=1) if tf == "1d" else pd.Timedelta(hours=1)
    W = W.loc[partenza - passo:]
    if len(W) < 2:
        return {"valore": capitale, "rendimento": 0.0, "pesi": {m: 0.0 for m in s["mercati"]}, "ordini": [], "equity": []}
    reg = []
    res = simula(con_ora, W, s.get("costi") or costi(s["mercati"]), capitale=capitale, soglia=s.get("soglia", 0.05),
                 candele_anno=365 if tf == "1d" else 365 * 24, registro=reg)
    eq = res.equity
    in_vigore = W.iloc[-2]          # deciso alla chiusura dell'ultima candela chiusa, eseguito ora
    return {"valore": float(eq.iloc[-1]), "rendimento": float(eq.iloc[-1] / capitale - 1),
            "pesi": {m: float(in_vigore[m]) for m in s["mercati"]},
            "ordini": [{**o, "tempo": _tempo(o["tempo"])} for o in reg],
            "equity": [[_tempo(t), round(float(v), 4)] for t, v in eq.items()],
            "costi": float(res.costi), **_operazioni(reg)}


def _migliaia(x: float) -> str:
    return f"{x:,.0f}".replace(",", ".")


def prossimo_passo(chiusi: dict) -> str:
    """Cosa deve succedere perché il canale gaussiano su bitcoin (profili 1, 2 e 3) cambi posizione."""
    d = {"BTC_EUR": chiusi["1d"]["BTC_EUR"]}
    x = ST.canale_gaussiano(d, "BTC_EUR", periodo=144, molt=2.0, livelli=True).iloc[-1]
    c = float(d["BTC_EUR"]["close"].iloc[-1])
    banda = float(x["banda_alta"])
    dist = (f"{(c / banda - 1) * 100:+.1f}".replace(".", ",")) + "%"
    if x["BTC_EUR"] > 0:
        return (f"Il canale su bitcoin è DENTRO: esce se una chiusura giornaliera scende sotto {_migliaia(banda)} € "
                f"(ultima chiusura {_migliaia(c)} €, {dist} dalla banda).")
    filtro = "in salita" if x["sale"] > 0 else "in discesa"
    return (f"Il canale su bitcoin è FUORI: rientra se chiude sopra {_migliaia(banda)} € con il filtro in salita "
            f"(ultima chiusura {_migliaia(c)} €, {dist} dalla banda; filtro {filtro}).")


def storico(s: dict, dati_giornalieri: dict, dati_orari: dict, capitale: float = 100.0) -> dict:
    """La stessa strategia provata sul passato: dal 2018 (giornaliere) o da luglio 2021 (a 1 ora)."""
    if s["id"] == "p5":          # monete: storico Binance in euro (torneo 2), con le monete crollate
        sys.path.insert(0, os.path.join(RADICE, "torneo"))
        import torneo2
        dati = {f"{m}_EUR": v for m, v in torneo2.carica().items()}
        inizio = "2018-03-01"
        W = s["f"](dati).fillna(0.0)
        W = W.loc[:, (W != 0).any()].loc[inizio:]
        reg = []
        res = simula({m: dati[m] for m in W.columns}, W, costi(W.columns), capitale=capitale, soglia=0.05, registro=reg)
        return _storico_da(res, reg, inizio, capitale)
    if s["tf"] == "1d":
        dati = {m: dati_giornalieri[m] for m in s["mercati"]}
        inizio = "2018-01-01"
    else:
        dati = {m: dati_orari[m] for m in s["mercati"]}
        inizio = "2021-07-01"
    W = s["f"](dati).fillna(0.0)
    for m in s["mercati"]:
        if m not in W.columns:
            W[m] = 0.0
    W = W[s["mercati"]].loc[inizio:]
    reg = []
    res = simula(dati, W, s.get("costi") or costi(s["mercati"]), capitale=capitale, soglia=s.get("soglia", 0.05),
                 candele_anno=365 if s["tf"] == "1d" else 365 * 24, registro=reg)
    return _storico_da(res, reg, inizio, capitale)


def _storico_da(res, reg, inizio, capitale: float = 100.0) -> dict:
    eq = res.equity
    gg = eq.groupby(eq.index.normalize()).last()
    sett = gg.resample("W-SUN").last().dropna()
    anni = {}
    for a, g in gg.groupby(gg.index.year):
        prec = gg[gg.index.year < a]
        partenza = prec.iloc[-1] if len(prec) else g.iloc[0]
        anni[int(a)] = float(g.iloc[-1] / partenza - 1)
    # valore a fine giornata per ogni giorno di calendario (i giorni senza candela tengono quello del giorno prima):
    # serve alla pagina per i filtri di periodo, i mesi e il confronto tra strategie. «base» = capitale di partenza.
    cal = gg.reindex(pd.date_range(gg.index[0], gg.index[-1], freq="D")).ffill()
    return {"inizio": inizio, "metriche": {**_metriche(gg, 1), "costi_eur": float(res.costi), "ordini": res.n_ordini,
                                           **_operazioni(reg)},
            "anni": anni, "equity": [[_tempo(t), round(float(v), 2)] for t, v in sett.items()],
            "ordini": [{**o, "tempo": _tempo(o["tempo"])} for o in reg],
            "giorni": {"dal": cal.index[0].strftime("%Y-%m-%d"), "base": float(capitale),
                       "v": [float(f"{float(v):.6g}") for v in cal.values]}}   # 6 cifre: precisione 0,001%
