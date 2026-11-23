# myLocalTuya

Controllo **locale** dei dispositivi Tuya / Smart Life in Home Assistant, con una configurazione ridotta al minimo:

1. inserisci il **codice utente** dell'app Smart Life;
2. inquadri il **QR code** con l'app;
3. scegli i dispositivi. Fine.

Niente account sviluppatore su iot.tuya.com, niente local key da copiare a mano, niente DP da mappare.

## Come funziona

| Fase | Cosa succede |
|---|---|
| Configurazione | Il cloud Tuya viene usato **una volta** per scaricare elenco dispositivi, local key e tabella dei DP (id locale ↔ funzione). |
| Rete locale | IP e versione del protocollo (3.1 / 3.3 / 3.4 / 3.5) vengono rilevati dai broadcast UDP dei dispositivi; se la versione salvata è sbagliata, viene trovata da sola. |
| Entità | Vengono create automaticamente in base alla categoria e alle funzioni del dispositivo: prese (switch, potenza, corrente, tensione, energia), luci (luminosità, temperatura colore, colore), condizionatori/termostati (climate), più switch/select/number/sensori per le altre funzioni. |
| Uso quotidiano | Connessione TCP persistente con ogni dispositivo: gli aggiornamenti arrivano in tempo reale (push), i comandi partono subito. **Nessun cloud.** |
| Auto-riparazione | Se l'IP cambia viene ripreso dai broadcast; se la local key cambia (dispositivo riassociato) viene riscaricata automaticamente dal cloud. |

Il sensore **Energia** delle prese accumula i consumi anche quando il dispositivo azzera il suo contatore interno, quindi è pronto per la dashboard Energia e per l'helper *Contatore utenze* (consumi giornalieri/mensili/annuali).

## Installazione (HACS)

1. HACS → menu ⋮ → *Repository personalizzati* → aggiungi l'URL di questo repository, categoria **Integrazione**.
2. Installa **myLocalTuya** e riavvia Home Assistant.
3. *Impostazioni → Dispositivi e servizi → Aggiungi integrazione → myLocalTuya*.

Il codice utente si trova nell'app Smart Life in *Io → Impostazioni → Account e sicurezza → Codice utente*.

## Opzioni

- **Aggiorna dispositivi e chiavi dal cloud**: per aggiungere/rimuovere dispositivi o dopo averne riassociato uno.
- **Imposta IP manualmente**: solo se i broadcast UDP non arrivano (es. Home Assistant in Docker senza `network_mode: host`). Servono le porte UDP 6666, 6667 e 7000.
- **Rifai l'accesso con il QR code**: se il token cloud è scaduto.

Può convivere con Tuya / Xtend Tuya mentre lo provi (i dispositivi compariranno due volte).

## Hub e sotto-dispositivi

I dispositivi Zigbee/Bluetooth collegati a un hub Tuya (es. timer irrigazione) sono comandati **in locale tramite l'hub**, sulla stessa connessione:

- l'hub viene aggiunto automaticamente quando scegli un suo sotto-dispositivo;
- il *node id* del sotto-dispositivo viene preso dal cloud oppure rilevato dall'hub (se l'hub ha un solo sotto-dispositivo non riconosciuto);
- lo stato online/offline arriva dall'hub; se un sotto-dispositivo a batteria "dorme", può risultare non disponibile finché non si risveglia.

Se il collegamento automatico non riesce, in *Opzioni → Imposta IP o hub manualmente* puoi scegliere l'hub e inserire il node id (la schermata mostra quelli visti dall'hub e non ancora assegnati).

## Limiti attuali

- Le funzioni di tipo Json/Raw (scene, musica, programmazioni) non vengono mappate automaticamente.

## Diagnostica

*Impostazioni → Dispositivi e servizi → myLocalTuya → ⋮ → Scarica diagnostica* produce un file con tabella DP, stato e ultimo errore di ogni dispositivo (local key e token oscurati): utile per segnalare un dispositivo mappato male.

## Sviluppo

```bash
pip install -r requirements_test.txt
pytest
```
