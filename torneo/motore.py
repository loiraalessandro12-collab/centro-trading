"""Motore del torneo: simula un portafoglio a partire dai PESI decisi dalla strategia.

Regola d'oro (niente sbirciate nel futuro): il peso deciso alla CHIUSURA della candela t
viene eseguito all'APERTURA della candela t+1, pagando commissione e slittamento.

Glossario:
- peso: valore della posizione diviso il capitale. 1 = tutto il capitale su quel mercato;
  0,5 = metà; 0 = fuori; negativo = scommessa al ribasso (solo dove si può: CFD).
- commissione: quanto prende la piattaforma a ogni acquisto o vendita (per lato).
- slittamento (slippage): il prezzo peggiore che si ottiene davvero rispetto a quello del grafico.
- finanziamento: il costo giornaliero di una posizione a leva o di un CFD tenuto di notte.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class Costi:
    commissione: float = 0.004       # per lato (0,004 = 0,40%)
    slittamento: float = 0.0005      # per lato
    fin_long_annuo: float = 0.0      # CFD: costo annuo della posizione long sul valore
    fin_short_annuo: float = 0.0
    minimo_ordine_eur: float = 0.0   # ordini più piccoli vengono saltati (realismo con 100 euro)

    def scala(self, k: float) -> "Costi":
        return Costi(self.commissione * k, self.slittamento * k, self.fin_long_annuo * k,
                     self.fin_short_annuo * k, self.minimo_ordine_eur)


@dataclass
class Risultato:
    equity: pd.Series                 # valore del conto a fine candela
    rendimenti: pd.Series             # rendimento di ogni candela
    esposizione: pd.Series            # somma dei |pesi| a fine candela
    costi: float                      # costi totali pagati (in euro)
    operazioni: list = field(default_factory=list)   # ogni operazione chiusa: rendimento sul capitale
    n_ordini: int = 0
    ordini_sotto_minimo: int = 0


def allinea(prezzi: dict[str, pd.DataFrame], pesi: pd.DataFrame) -> tuple[pd.DatetimeIndex, dict, dict]:
    """Mette tutti i mercati sullo stesso calendario (quello dei pesi). Dove un mercato non ha
    la candela (es. oro nel fine settimana) si ripete l'ultimo prezzo: per PAXG, che si scambia
    sempre, è un'approssimazione piccola."""
    idx = pesi.index
    O, C = {}, {}
    for m in pesi.columns:
        df = prezzi[m].reindex(idx)
        c = df["close"].ffill()
        o = df["open"].where(df["open"].notna(), c.shift(1)).ffill()
        O[m], C[m] = o.to_numpy(dtype=float), c.to_numpy(dtype=float)
    return idx, O, C


def simula(prezzi: dict[str, pd.DataFrame], pesi: pd.DataFrame, costi: dict[str, Costi],
           capitale: float = 100.0, soglia: float = 0.02, candele_anno: float = 365.0,
           molt_costi: float = 1.0, registro: list | None = None) -> Risultato:
    """Simula candela per candela.
    soglia: si ribilancia un mercato solo se il peso desiderato si discosta da quello attuale di più
    di 'soglia' (in valore assoluto); entrate e uscite complete si fanno sempre.
    registro: se è una lista, ci finisce ogni ordine eseguito (data, mercato, compra/vendi, prezzo,
    importo in euro, costo, peso prima e dopo). Non cambia i conti."""
    pesi = pesi.fillna(0.0)
    idx, O, C = allinea(prezzi, pesi)
    mercati = list(pesi.columns)
    W = pesi.to_numpy(dtype=float)
    n, k = W.shape
    cs = {m: costi[m].scala(molt_costi) for m in mercati}
    cassa = capitale
    unita = np.zeros(k)
    eq = np.full(n, np.nan)
    esp = np.zeros(n)
    costi_tot = 0.0
    n_ordini = sotto = 0
    # contabilità delle operazioni (per mercato): capitale all'ingresso e profitto accumulato
    pnl_aperto = np.zeros(k)
    cap_ingresso = np.zeros(k)
    operazioni = []
    prezzo_prec = np.array([C[m][0] for m in mercati])
    eq[0] = capitale
    for i in range(1, n):
        ap = np.array([O[m][i] for m in mercati])
        ch = np.array([C[m][i] for m in mercati])
        ok = np.isfinite(ap) & np.isfinite(ch) & (ap > 0)
        # 1) movimento dalla chiusura precedente all'apertura (salto della notte / del weekend)
        pnl_aperto += np.where(ok & np.isfinite(prezzo_prec), unita * (ap - prezzo_prec), 0.0)
        valore_ap = cassa + float(np.nansum(np.where(ok, unita * ap, unita * prezzo_prec)))
        # 2) esecuzione dei pesi decisi alla chiusura precedente
        w_obj = W[i - 1]
        for j, m in enumerate(mercati):
            if not ok[j] or valore_ap <= 0:
                continue
            w_att = unita[j] * ap[j] / valore_ap
            entra_esce = (w_obj[j] == 0.0 and unita[j] != 0.0) or (w_obj[j] != 0.0 and unita[j] == 0.0)
            gira = np.sign(w_obj[j]) != np.sign(w_att) and w_obj[j] != 0.0 and unita[j] != 0.0
            if not (entra_esce or gira or abs(w_obj[j] - w_att) > soglia):
                continue
            valore = (w_obj[j] - w_att) * valore_ap
            if valore > 0:
                # sugli acquisti la commissione si paga con gli stessi soldi: con 100 euro si compra
                # per 100/(1+costo), così la cassa non va mai sotto zero (niente leva nascosta)
                valore /= 1.0 + cs[m].commissione + cs[m].slittamento
            if w_obj[j] == 0.0:
                valore = -unita[j] * ap[j]          # chiusura completa, esatta
            if abs(valore) < cs[m].minimo_ordine_eur and w_obj[j] != 0.0:
                sotto += 1
                continue
            costo = abs(valore) * (cs[m].commissione + cs[m].slittamento)
            if unita[j] == 0.0:
                cap_ingresso[j] = valore_ap
                pnl_aperto[j] = 0.0
            unita_nuove = unita[j] + valore / ap[j]
            cassa -= valore + costo
            costi_tot += costo
            pnl_aperto[j] -= costo
            n_ordini += 1
            if registro is not None:
                registro.append({"tempo": idx[i], "mercato": m, "lato": "compra" if valore > 0 else "vendi",
                                 "prezzo": float(ap[j]), "importo_eur": float(abs(valore)), "costo_eur": float(costo),
                                 "peso_prima": float(w_att), "peso_dopo": float(w_obj[j]),
                                 "conto_eur": float(valore_ap)})
            if w_obj[j] == 0.0 or gira:
                operazioni.append(pnl_aperto[j] / cap_ingresso[j] if cap_ingresso[j] > 0 else 0.0)
                pnl_aperto[j] = 0.0
                cap_ingresso[j] = valore_ap if gira else 0.0
                unita_nuove = 0.0 if w_obj[j] == 0.0 else unita_nuove
            unita[j] = unita_nuove
        # 3) finanziamento delle posizioni tenute (CFD / margine), sul valore a fine candela
        for j, m in enumerate(mercati):
            if unita[j] == 0.0 or not ok[j]:
                continue
            tasso = cs[m].fin_long_annuo if unita[j] > 0 else cs[m].fin_short_annuo
            if tasso:
                f = abs(unita[j] * ch[j]) * tasso / candele_anno
                cassa -= f
                costi_tot += f
                pnl_aperto[j] -= f
        # 4) movimento dall'apertura alla chiusura
        pnl_aperto += np.where(ok, unita * (ch - ap), 0.0)
        prezzo_prec = np.where(ok, ch, prezzo_prec)
        valore_ch = cassa + float(np.nansum(unita * prezzo_prec))
        eq[i] = valore_ch
        esp[i] = float(np.nansum(np.abs(unita * prezzo_prec))) / valore_ch if valore_ch > 0 else 0.0
        if valore_ch <= 0:      # conto azzerato: si ferma tutto
            eq[i:] = max(valore_ch, 0.0)
            break
    # operazioni ancora aperte a fine storia: si contano al valore di chiusura
    for j in range(k):
        if unita[j] != 0.0 and cap_ingresso[j] > 0:
            operazioni.append(pnl_aperto[j] / cap_ingresso[j])
    e = pd.Series(eq, index=idx).ffill()
    r = e.pct_change().fillna(0.0)
    return Risultato(equity=e, rendimenti=r, esposizione=pd.Series(esp, index=idx), costi=costi_tot,
                     operazioni=operazioni, n_ordini=n_ordini, ordini_sotto_minimo=sotto)
