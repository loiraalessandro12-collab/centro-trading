# Centro di controllo — dati dal vivo, soldi finti

> **Simulazione con soldi virtuali.** I risultati passati non garantiscono quelli futuri.
> Il centro legge solo i prezzi pubblici di Kraken: niente account, niente chiavi, niente ordini veri.

## Come si apre

**Doppio clic su `avvia_centro.bat`.** Si apre una finestra nera (il «server») e poi il browser su
`http://127.0.0.1:8787`. **Lascia aperta la finestra nera**: chiudendola il centro si ferma. La pagina
è raggiungibile solo da questo PC.

**All'avvio aspetta circa un minuto.** La pagina scrive cosa sta facendo («Avvio in corso: …»). Prezzi
e prime 15 strategie arrivano in 10-20 secondi; il profilo 5 entra dopo circa 70 secondi, perché scarica
46 monete una alla volta (Kraken accetta circa una richiesta al secondo). Chiudere e riaprire non fa
perdere niente: l'arena viene ricalcolata dalle candele, dalla partenza in avanti.

## Cosa c'è nella pagina

- **In alto**: prezzi dal vivo di bitcoin, ether, solana e oro in token (PAXG), aggiornati ogni minuto,
  e i pulsanti del budget (100, 1.000, 10.000, 100.000 €).
- **Dal vivo**: l'ARENA. Tutte le strategie girano insieme, da oggi in avanti, con soldi finti:
  i 4 profili del bot, alcune regole classiche, le strategie dei video (per vedere dal vivo perché
  perdono) e due confronti (bitcoin tenuto, metà bitcoin e metà oro). Toccando una riga si vedono il
  grafico con le frecce di acquisto e vendita, il valore del conto e l'elenco dei trade.
- **Storico**: ogni strategia provata sul passato (dal 2018; da luglio 2021 quelle a 1 ora), con il
  grafico del conto, gli anni, i trade e il prezzo con le entrate e le uscite.
- **Budget**: quanto sarebbero diventati 100, 1.000, 10.000 e 100.000 €, prima e dopo le tasse.
- **Come migliorarlo**: le prove fatte (commissioni, conferma, oro dosato, velocità) con i numeri.
- **Video analizzati**: cosa dicevano i video e cosa ne è uscito.

## Da sapere

- Le strategie giornaliere decidono una volta al giorno, alle 00:00 UTC (l'1 di notte in Italia
  d'inverno, le 2 d'estate): **possono passare settimane senza un trade**. Quelle a 1 ora decidono a ogni
  ora piena e fanno molti trade (e perdono, per le commissioni: è il motivo per cui sono lì).
- Se il PC resta spento, alla riapertura il centro scarica le candele mancanti (fino a 30 giorni per
  quelle orarie, 720 giorni per le giornaliere) e rifà i conti dalla partenza: arriva allo stesso punto.
- La partenza dell'arena si cambia in `config_centro.json` (campo `partenza`).
- File: `server.py` (il server), `arena.py` (l'elenco delle strategie), `index.html` (la pagina),
  `scrivi_contenuti.py` → `contenuti.json` (i testi delle schede), `dati/` (cache dei prezzi).
