"""ESPORTA il centro di controllo come SITO STATICO (cartella con pagina + file JSON), senza server.

Fa un giro completo (candele e prezzi da Kraken, arena, storico) e scrive tutto in una cartella che si può
pubblicare dove si vuole: un sito gratuito, un'app che legge gli stessi file, o anche solo il PC.
È il pezzo che un servizio esterno (per esempio GitHub, ogni ora) può lanciare da solo, anche a PC spento.

Uso: python -B centro/esporta.py [cartella]      (predefinita: centro/sito)
"""
from __future__ import annotations

import json
import os
import shutil
import sys

QUI = os.path.dirname(os.path.abspath(__file__))
RADICE = os.path.dirname(QUI)
sys.path.insert(0, RADICE)

from centro import server as SV  # noqa: E402


def scrivi(cartella: str, nome: str, obj) -> None:
    with open(os.path.join(cartella, nome), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, default=SV._num, separators=(",", ":"))


def carica_storico_fisso(cartella: str) -> None:
    """Lo storico (prove dal 2018) salvato da centro/prepara_storico.py."""
    for s in SV.A.STRATEGIE:
        p = os.path.join(cartella, f"storico_{s['id']}.json")
        if os.path.exists(p):
            SV.S.storico[s["id"]] = json.load(open(p, encoding="utf-8"))
    for m in ("BTC_EUR", "ORO_EUR"):
        p = os.path.join(cartella, f"storiche_{m}.json")
        if os.path.exists(p):
            SV.S.candele_storiche[m] = json.load(open(p, encoding="utf-8"))
    SV.S.pronto_storico = True


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(QUI, "sito")
    dati = os.path.join(out, "dati")
    os.makedirs(dati, exist_ok=True)
    import ccxt
    ex = ccxt.kraken({"enableRateLimit": True, "timeout": 20000})
    SV.aggiorna_candele(ex)
    SV.aggiorna_monete(ex)
    SV.aggiorna_prezzi(ex)
    SV.ricalcola_arena()
    fisso = os.path.join(QUI, "storico_fisso")
    if not os.path.isdir(os.path.join(RADICE, "torneo", "dati")) and os.path.isdir(fisso):
        carica_storico_fisso(fisso)     # su GitHub i dati storici non ci sono: usa lo storico calcolato sul PC
    else:
        SV.calcola_storico()
    SV.S.fase = None
    for b in SV.BUDGET:
        scrivi(dati, f"stato_{b}.json", SV.riassunto(b))
        for s in SV.A.STRATEGIE:
            scrivi(dati, f"arena_{s['id']}_{b}.json", SV.S.arena.get(b, {}).get(s["id"], {}))
    for s in SV.A.STRATEGIE:   # i valori giorno per giorno vanno tutti insieme in storico_tutte.json
        scrivi(dati, f"storico_{s['id']}.json", {k: v for k, v in (SV.S.storico.get(s["id"]) or {}).items() if k != "giorni"})
    scrivi(dati, "storico_tutte.json", SV.storico_tutte())
    for tf in ("1h", "1d"):
        for m in SV.A.MERCATI:
            df = SV.S.chiusi[tf].get(m)
            righe = [] if df is None else [[int(t.timestamp()), r.open, r.high, r.low, r.close]
                                           for t, r in df.tail(720 if tf == "1h" else 400).iterrows()]
            c = SV.S.correnti[tf].get(m)
            if c:
                righe.append([int(c["tempo"].timestamp()), c["open"], c["high"], c["low"], c["close"]])
            scrivi(dati, f"candele_{m}_{tf}.json", righe)
    for m in ("BTC_EUR", "ORO_EUR"):
        scrivi(dati, f"storiche_{m}.json", SV.S.candele_storiche.get(m, []))
    scrivi(dati, "info.json", {"budget": SV.leggi_json("torneo", "risultati", "budget.json"),
                               "miglioramenti": SV.leggi_json("torneo", "risultati", "miglioramenti.json"),
                               "miglioramenti2": SV.leggi_json("torneo", "risultati", "miglioramenti2.json"),
                               "contenuti": SV.leggi_json("centro", "contenuti.json")})
    # la pagina: la stessa del centro, in modalità «statica» (legge i file invece del server)
    html = open(os.path.join(QUI, "index.html"), encoding="utf-8").read()
    html = html.replace("<head>", '<head>\n<script>window.STATICO = true;</script>\n<link rel="manifest" href="manifest.json">\n'
                        '<link rel="apple-touch-icon" href="icona.png">\n<meta name="apple-mobile-web-app-capable" content="yes">\n'
                        '<meta name="theme-color" content="#0e151d">', 1)
    open(os.path.join(out, "index.html"), "w", encoding="utf-8").write(html)
    scrivi(out, "manifest.json", {"name": "Centro di controllo", "short_name": "Trading", "start_url": ".",
                                  "display": "standalone", "background_color": "#0e151d", "theme_color": "#0e151d",
                                  "icons": [{"src": "icona.png", "sizes": "512x512", "type": "image/png"}]})
    icona = os.path.join(QUI, "icona.png")
    if os.path.exists(icona):
        shutil.copy(icona, os.path.join(out, "icona.png"))
    print(f"Sito esportato in {out}: {len(os.listdir(dati))} file di dati", flush=True)


if __name__ == "__main__":
    main()
