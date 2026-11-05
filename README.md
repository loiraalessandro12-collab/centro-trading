# Centro di controllo · trading con soldi finti

Una gara fra 16 strategie di trading con **soldi virtuali**, partita il 4 novembre 2026.
Ogni ora GitHub prende i prezzi pubblici di Kraken, rifà i conti e aggiorna la pagina:

**https://loiraalessandro12-collab.github.io/centro-trading/**

- Niente conti, niente chiavi, niente ordini veri.
- Le decisioni si prendono solo su candele già chiuse; commissioni e slittamento sono inclusi.
- Lo storico (prove dal 2018) è calcolato una volta sul PC e salvato in `centro/storico_fisso/`.
- I risultati passati non garantiscono quelli futuri. Non è un consiglio di investimento.

## Come gira

`.github/workflows/centro.yml` lancia `centro/esporta.py` ogni ora (al minuto 7; GitHub può ritardare
di qualche minuto), salva le candele nuove in `centro/dati/` e pubblica la pagina con GitHub Pages.
Si può lanciare anche a mano da **Actions → centro-di-controllo → Run workflow**.

Il codice arriva da una cartella sul PC con `centro/pubblica.py`: le modifiche si fanno lì, non qui.
