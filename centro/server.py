"""CENTRO DI CONTROLLO — server locale (raggiungibile solo da questo PC) con dati dal vivo.

Cosa fa finché resta aperto:
  - ogni minuto legge i prezzi pubblici di Kraken (bitcoin, ether, solana, oro in token PAXG);
  - ogni 5 minuti aggiorna le candele da 1 ora e da 1 giorno e le salva in centro/dati (solo in aggiunta);
  - ogni minuto rifà i conti dell'ARENA: tutte le strategie con soldi finti, da oggi in avanti, con
    budget da 100, 1.000, 10.000 e 100.000 euro;
  - all'avvio calcola lo STORICO di ogni strategia (dal 2018, o da luglio 2021 per quelle a 1 ora).
La pagina è http://127.0.0.1:8787 . Niente account, niente chiavi, niente ordini veri.

Uso: python -B centro/server.py        (oppure doppio clic su centro/avvia_centro.bat)
"""
from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pandas as pd

QUI = os.path.dirname(os.path.abspath(__file__))
RADICE = os.path.dirname(QUI)
sys.path.insert(0, RADICE)

from centro import arena as A  # noqa: E402

CONFIG = json.load(open(os.path.join(QUI, "config_centro.json"), encoding="utf-8"))
CACHE = os.path.join(QUI, "dati")
os.makedirs(CACHE, exist_ok=True)
BUDGET = (100, 1_000, 10_000, 100_000)
DURATA = {"1h": pd.Timedelta(hours=1), "1d": pd.Timedelta(days=1)}


class Stato:
    def __init__(self):
        self.lock = threading.Lock()
        self.prezzi = {}
        self.chiusi = {"1h": {}, "1d": {}}
        self.correnti = {"1h": {}, "1d": {}}
        self.arena = {}
        self.storico = {}
        self.candele_storiche = {}
        self.errori = []
        self.agg_prezzi = None
        self.agg_candele = None
        self.agg_arena = None
        self.pronto_storico = False
        self.secondi_calcolo = None
        self.prossimo = None
        self.fase = "mi collego a Kraken"      # cosa sta facendo adesso (la pagina lo mostra durante l'avvio)

    def errore(self, msg: str):
        with self.lock:
            self.errori = ([{"quando": _ora_iso(), "messaggio": msg}] + self.errori)[:20]
        print("ERRORE:", msg, flush=True)


S = Stato()


def _ora_utc() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC").tz_localize(None)


def _riprova(fn, *args, tentativi: int = 3, **kw):
    """Kraken a volte risponde «troppe richieste» o non risponde in tempo: riprova con pause crescenti."""
    for i in range(tentativi):
        try:
            return fn(*args, **kw)
        except Exception:  # noqa: BLE001
            if i == tentativi - 1:
                raise
            time.sleep(3 * (i + 1))


def _ora_iso() -> str:
    """Adesso, con il fuso orario scritto: la pagina mostra l'ora giusta anche quando i conti
    li fa GitHub (che lavora in ora UTC)."""
    return pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds")


def _leggi_cache(m: str, tf: str) -> pd.DataFrame:
    p = os.path.join(CACHE, f"{m}_{tf}.csv")
    if not os.path.exists(p):
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"], dtype=float)
    return pd.read_csv(p, index_col="tempo", parse_dates=["tempo"]).astype(float)


def aggiorna_candele(ex) -> None:
    adesso = _ora_utc()
    n, fatti = 2 * len(A.MERCATI), 0
    for tf in ("1h", "1d"):
        for m, simbolo in A.MERCATI.items():
            fatti += 1
            S.fase = f"scarico le candele di bitcoin, ether, solana e oro ({fatti} di {n})"
            righe = _riprova(ex.fetch_ohlcv, simbolo, tf, limit=720)
            df = pd.DataFrame(righe, columns=["t", "open", "high", "low", "close", "volume"])
            df.index = pd.DatetimeIndex(pd.to_datetime(df["t"], unit="ms"), name="tempo")
            df = df[["open", "high", "low", "close", "volume"]].astype(float)
            chiuse = df[df.index + DURATA[tf] <= adesso]
            in_corso = df[df.index + DURATA[tf] > adesso]
            vecchio = _leggi_cache(m, tf)
            nuove = chiuse[~chiuse.index.isin(vecchio.index)]
            tutto = pd.concat([vecchio, nuove]).sort_index() if len(vecchio) else nuove
            if len(nuove):
                tutto.to_csv(os.path.join(CACHE, f"{m}_{tf}.csv"), index_label="tempo")
            corrente = None
            if len(in_corso):
                r = in_corso.iloc[-1]
                corrente = {"tempo": in_corso.index[-1], "open": float(r["open"]), "high": float(r["high"]),
                            "low": float(r["low"]), "close": float(r["close"]), "volume": float(r["volume"])}
            with S.lock:
                S.chiusi[tf][m] = tutto
                S.correnti[tf][m] = corrente
            time.sleep(0.6)
    S.agg_candele = _ora_iso()


def aggiorna_monete(ex) -> None:
    """Candele giornaliere delle monete del profilo 5 (solo quelle quotate in euro su Kraken)."""
    adesso = _ora_utc()
    if not ex.markets:
        S.fase = "leggo l'elenco delle monete in euro su Kraken"
        _riprova(ex.load_markets)
    da_fare = [(m, simbolo) for m, simbolo in A.MERCATI_ALT.items() if simbolo in ex.markets and m not in A.MERCATI]
    for i, (m, simbolo) in enumerate(da_fare, 1):
        S.fase = f"scarico le monete del profilo 5 ({i} di {len(da_fare)})"
        try:
            righe = _riprova(ex.fetch_ohlcv, simbolo, "1d", limit=720)
        except Exception as e:  # noqa: BLE001
            S.errore(f"candele {simbolo}: {type(e).__name__}")
            continue
        df = pd.DataFrame(righe, columns=["t", "open", "high", "low", "close", "volume"])
        df.index = pd.DatetimeIndex(pd.to_datetime(df["t"], unit="ms"), name="tempo")
        df = df[["open", "high", "low", "close", "volume"]].astype(float)
        chiuse = df[df.index + DURATA["1d"] <= adesso]
        in_corso = df[df.index + DURATA["1d"] > adesso]
        vecchio = _leggi_cache(m, "1d")
        nuove = chiuse[~chiuse.index.isin(vecchio.index)]
        tutto = pd.concat([vecchio, nuove]).sort_index() if len(vecchio) else nuove
        if len(nuove):
            tutto.to_csv(os.path.join(CACHE, f"{m}_1d.csv"), index_label="tempo")
        corrente = None
        if len(in_corso):
            r = in_corso.iloc[-1]
            corrente = {"tempo": in_corso.index[-1], "open": float(r["open"]), "high": float(r["high"]),
                        "low": float(r["low"]), "close": float(r["close"]), "volume": float(r["volume"])}
        with S.lock:
            S.chiusi["1d"][m] = tutto
            S.correnti["1d"][m] = corrente
        time.sleep(0.6)
    disponibili = [m for m in A.MERCATI_ALT if m in S.chiusi["1d"] and len(S.chiusi["1d"][m])]
    for s in A.STRATEGIE:
        if s["id"] == "p5":
            s["mercati"] = ["BTC_EUR"] + [m for m in disponibili if m != "BTC_EUR"]


def aggiorna_prezzi(ex) -> None:
    t = _riprova(ex.fetch_tickers, list(A.MERCATI.values()))
    inverso = {v: k for k, v in A.MERCATI.items()}
    with S.lock:
        for simbolo, x in t.items():
            m = inverso.get(simbolo)
            if not m or x.get("last") is None:
                continue
            S.prezzi[m] = {"prezzo": float(x["last"]), "variazione_24h": x.get("percentage"),
                           "nome": A.NOMI_MERCATI[m]}
            # il prezzo di adesso diventa la chiusura provvisoria della candela in corso
            for tf in ("1h", "1d"):
                c = S.correnti[tf].get(m)
                if c:
                    c["close"] = float(x["last"])
                    c["high"] = max(c["high"], float(x["last"]))
                    c["low"] = min(c["low"], float(x["last"]))
    S.agg_prezzi = _ora_iso()


def ricalcola_arena() -> None:
    t0 = time.time()
    with S.lock:
        chiusi = {tf: dict(v) for tf, v in S.chiusi.items()}
        correnti = {tf: {m: (dict(c) if c else None) for m, c in v.items()} for tf, v in S.correnti.items()}
    if any(len(chiusi["1d"].get(m, [])) < 400 for m in ("BTC_EUR", "ORO_EUR")):
        return
    partenza = pd.Timestamp(CONFIG["partenza"])
    nuova = {}
    for b in BUDGET:
        nuova[b] = {}
        for s in A.STRATEGIE:
            if any(len(chiusi[s["tf"]].get(m, [])) == 0 for m in s["mercati"]):
                continue            # dati non ancora scaricati (es. monete del profilo 5 all'avvio): entra al giro dopo
            try:
                nuova[b][s["id"]] = A.esegui(s, chiusi, correnti, partenza, float(b))
            except Exception as e:  # noqa: BLE001
                S.errore(f"arena {s['id']}: {type(e).__name__}: {e}")
    try:
        prossimo = A.prossimo_passo(chiusi)
    except Exception as e:  # noqa: BLE001
        prossimo = None
        S.errore(f"prossimo passo: {e}")
    with S.lock:
        S.arena = nuova
        S.prossimo = prossimo
        S.agg_arena = _ora_iso()
        S.secondi_calcolo = round(time.time() - t0, 1)


def calcola_storico() -> None:
    try:
        from torneo.gara import carica_giornalieri, leggi
        d = carica_giornalieri()
        orari = {"BTC_EUR": leggi(os.path.join(RADICE, "dati", "BTC-EUR_1h.csv"))}
        for s in A.STRATEGIE:
            try:
                r = A.storico(s, d, orari)
                with S.lock:
                    S.storico[s["id"]] = r
            except Exception as e:  # noqa: BLE001
                S.errore(f"storico {s['id']}: {type(e).__name__}: {e}")
        for m in ("BTC_EUR", "ORO_EUR"):
            df = d[m].loc["2018-01-01":]
            S.candele_storiche[m] = [[int(t.timestamp()), round(r.open, 2), round(r.high, 2), round(r.low, 2), round(r.close, 2)]
                                     for t, r in df.iterrows()]
    except Exception as e:  # noqa: BLE001
        S.errore(f"storico: {type(e).__name__}: {e}")
    S.pronto_storico = True


def ciclo() -> None:
    import ccxt
    ex = ccxt.kraken({"enableRateLimit": True, "timeout": 20000})
    ultima_candela = 0.0
    ultime_monete = 0.0
    while True:
        try:
            if time.time() - ultima_candela >= CONFIG["aggiorna_candele_s"]:
                aggiorna_candele(ex)
                ultima_candela = time.time()
            S.fase = "leggo i prezzi e calcolo l'arena"
            aggiorna_prezzi(ex)
            ricalcola_arena()
            # le ~45 monete del profilo 5 (un'ora di validità) vengono DOPO: così all'avvio
            # le altre strategie compaiono in ~20 secondi invece di aspettare tutte le monete
            if time.time() - ultime_monete >= 3600:
                aggiorna_monete(ex)
                ultime_monete = time.time()
                S.fase = "leggo i prezzi e calcolo l'arena"
                aggiorna_prezzi(ex)
                ricalcola_arena()
        except Exception as e:  # noqa: BLE001
            S.errore(f"aggiornamento: {type(e).__name__}: {str(e)[:200]}")
        S.fase = None
        time.sleep(CONFIG["aggiorna_prezzi_s"])


# ------------------------------------------------------------------------------------------- risposte
def _num(x):
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    return x


def riassunto(budget: int) -> dict:
    with S.lock:
        arena = S.arena.get(budget, {})
        righe = []
        for s in A.STRATEGIE:
            r = arena.get(s["id"])
            if not r:
                continue
            ultimo = r["ordini"][-1] if r["ordini"] else None
            righe.append({"id": s["id"], "nome": s["nome"], "gruppo": s["gruppo"], "tf": s["tf"], "origine": s["origine"],
                          "descrizione": s["descrizione"], "valore": r["valore"], "rendimento": r["rendimento"],
                          "pesi": r["pesi"], "n_ordini": len(r["ordini"]), "ultimo_ordine": ultimo,
                          "costi": r.get("costi", 0.0)})
        avvisi = []
        adesso = _ora_utc()
        for tf, limite, nome in (("1d", pd.Timedelta(hours=26), "giornaliere"), ("1h", pd.Timedelta(hours=2), "orarie")):
            df = S.chiusi[tf].get("BTC_EUR")
            if df is not None and len(df) and adesso - (df.index[-1] + DURATA[tf]) > limite:
                avvisi.append(f"Candele {nome} vecchie: l'ultima chiusa è del {df.index[-1]} (UTC). Le decisioni aspettano dati nuovi.")
        return {"adesso": _ora_iso(), "partenza": CONFIG["partenza"],
                "prossimo_passo": S.prossimo, "avvisi_dati": avvisi,
                "budget": budget, "prezzi": S.prezzi, "arena": righe, "errori": S.errori[:5],
                "aggiornato": {"prezzi": S.agg_prezzi, "candele": S.agg_candele, "arena": S.agg_arena},
                "secondi_calcolo": S.secondi_calcolo, "storico_pronto": S.pronto_storico, "fase": S.fase,
                "strategie": [{k: s[k] for k in ("id", "nome", "gruppo", "tf", "origine", "descrizione", "mercati")}
                              for s in A.STRATEGIE]}


def leggi_json(*parti):
    p = os.path.join(RADICE, *parti)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


class Gestore(BaseHTTPRequestHandler):
    def _invia(self, corpo: bytes, tipo: str, codice: int = 200):
        self.send_response(codice)
        self.send_header("Content-Type", tipo)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def _json(self, obj, codice=200):
        self._invia(json.dumps(obj, ensure_ascii=False, default=_num).encode("utf-8"), "application/json; charset=utf-8", codice)

    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        budget = int(q.get("budget", 100)) if q.get("budget", "100").isdigit() else 100
        budget = budget if budget in BUDGET else 100
        try:
            if u.path in ("/", "/index.html"):
                self._invia(open(os.path.join(QUI, "index.html"), "rb").read(), "text/html; charset=utf-8")
            elif u.path == "/api/stato":
                self._json(riassunto(budget))
            elif u.path == "/api/arena":
                with S.lock:
                    r = S.arena.get(budget, {}).get(q.get("id", ""))
                self._json(r or {}, 200 if r else 404)
            elif u.path == "/api/storico":
                with S.lock:
                    r = S.storico.get(q.get("id", ""))
                self._json(r or {"in_calcolo": not S.pronto_storico}, 200)
            elif u.path == "/api/candele":
                m, tf = q.get("mercato", "BTC_EUR"), q.get("tf", "1h")
                with S.lock:
                    df = S.chiusi.get(tf, {}).get(m)
                    c = S.correnti.get(tf, {}).get(m)
                righe = [] if df is None else [[int(t.timestamp()), r.open, r.high, r.low, r.close]
                                               for t, r in df.tail(720 if tf == "1h" else 400).iterrows()]
                if c:
                    righe.append([int(pd.Timestamp(c["tempo"]).timestamp()), c["open"], c["high"], c["low"], c["close"]])
                self._json(righe)
            elif u.path == "/api/candele_storiche":
                self._json(S.candele_storiche.get(q.get("mercato", "BTC_EUR"), []))
            elif u.path == "/api/info":
                self._json({"budget": leggi_json("torneo", "risultati", "budget.json"),
                            "miglioramenti": leggi_json("torneo", "risultati", "miglioramenti.json"),
                            "miglioramenti2": leggi_json("torneo", "risultati", "miglioramenti2.json"),
                            "contenuti": leggi_json("centro", "contenuti.json")})
            else:
                self._invia(b"Pagina non trovata", "text/plain; charset=utf-8", 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._json({"errore": str(e)}, 500)

    def log_message(self, *args):
        pass


def _storico_dopo_arena() -> None:
    """Lo storico impegna il processore per circa un minuto: parte dopo la prima arena (al massimo dopo
    90 secondi), così all'avvio prezzi e gara dal vivo arrivano prima."""
    for _ in range(180):
        if S.agg_arena:
            break
        time.sleep(0.5)
    calcola_storico()


def gia_acceso(porta: int) -> bool:
    """True se sulla porta risponde già un centro (per esempio quello partito in automatico all'accensione)."""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", porta), timeout=1):
            return True
    except OSError:
        return False


def main():
    porta = int(CONFIG["porta"])
    if gia_acceso(porta):
        url = f"http://127.0.0.1:{porta}"
        print(f"Il centro è già acceso (di solito dall'avvio automatico): la pagina è {url}", flush=True)
        if "--senza-browser" not in sys.argv:
            webbrowser.open(url)
        sys.exit(3)                 # 3 = «già acceso»: avvio_automatico.bat non riprova
    threading.Thread(target=ciclo, daemon=True).start()
    threading.Thread(target=_storico_dopo_arena, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", porta), Gestore)
    url = f"http://127.0.0.1:{porta}"
    print(f"Centro di controllo su {url}  (chiudi questa finestra per fermarlo)", flush=True)
    if "--senza-browser" not in sys.argv:
        threading.Timer(2.0, lambda: webbrowser.open(url)).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
