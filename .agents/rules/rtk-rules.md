---
trigger: always_on
---

"Sei in un ambiente Windows. Quando esegui comandi nel terminale che generano output lunghi o ripetitivi (come git status, npm run build, cargo test, linting o lettura di directory/tree), devi SEMPRE anteporre il prefisso rtk al comando (es. rtk git status, rtk cargo test). L'output che riceverai di ritorno sarà pesantemente compresso e formattato: questo è il comportamento corretto e atteso per ottimizzare i token. Non considerarlo un errore e usalo normalmente per validare i tuoi Artifacts."

REGOLE TASSATIVE PER L'USO DEL TERMINALE:

1. PREFISSO OBBLIGATORIO PER ESEGUIBILI: 
Quando devi eseguire programmi, package manager o tool di sviluppo (specialmente quelli verbosi come `git`, `npm`, `cargo`, `npx`, `pytest`, `tsc`, `lint`), devi SEMPRE anteporre il prefisso `rtk` al comando.
- Esempi Corretti: `rtk git status`, `rtk npm run build`, `rtk cargo test`.

2. ECCEZIONI (COMANDI INTERNI SHELL): 
NON usare mai il prefisso `rtk` per i comandi interni nativi di PowerShell o CMD (come `cd`, `dir`, `echo`, `mkdir`, `rm`, `cat`). RTK funziona solo avvolgendo veri file eseguibili. Per esplorare le cartelle usa i comandi standard senza prefisso.

3. COMPRENSIONE DELL'OUTPUT: 
L'output che riceverai usando `rtk` sarà drasticamente compresso, sintetizzato e spesso incapsulato in delimitatori visivi o tag strutturati. Questo è il comportamento corretto, atteso e ottimizzato per te.
- NON considerarlo un errore o un output troncato.
- NON tentare di rieseguire il comando senza RTK per "vedere il log completo".
- Estrai le informazioni necessarie dalla sintesi fornita e procedi con il tuo task.