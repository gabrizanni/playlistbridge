# Guida Windows a PlaylistBridge

Questa guida accompagna un trasferimento da Spotify a YouTube Music sullo stesso PC.
PlaylistBridge lavora localmente sui metadati: **non scarica né copia file audio** e non
richiede un servizio web gestito dal progetto.

## 1. Preparazione

1. Scarica l'artifact `PlaylistBridge-windows-x64` da GitHub Actions oppure ricevi lo ZIP
   da una persona fidata.
2. Estrai **tutto** `PlaylistBridge-windows-x64.zip` in una cartella, per esempio
   `Documenti\PlaylistBridge`.
3. Avvia `PlaylistBridge.exe`. Windows SmartScreen può segnalare una build non firmata:
   continua soltanto se lo ZIP proviene dal repository o da una persona fidata.
4. Fai la prima prova con una playlist privata di 10–20 brani, non con l'intera libreria.

La distribuzione è one-folder: l'EXE deve restare insieme ai file estratti. Per la versione
da sorgente servono Python 3.12 e i comandi indicati nel README.

## 2. Verificare la copia da Spotify Desktop

La copia dagli appunti può cambiare in base alla versione di Spotify Desktop. Prima del
trasferimento completo, fai questa prova:

1. Apri Spotify Desktop, poi una playlist.
2. Clicca una traccia nell'elenco.
3. Premi `Ctrl+A`, quindi `Ctrl+C`.
4. Incolla temporaneamente in Blocco note con `Ctrl+V`.

Il test è positivo se compaiono più righe, URI `spotify:track:...`, link
`https://open.spotify.com/track/...` oppure testo con titolo e artista. Non importa il
formato esatto: l'importatore deve mostrare quante righe valide riconosce prima di salvare.

Se Blocco note resta vuoto, prova a selezionare manualmente un gruppo di tracce e ripeti.
Se anche questo non funziona, passa alla sezione "Fallback: Account data Spotify". Evita
estensioni o siti che chiedono cookie Spotify o Google.

## 3. Importare le playlist

Importa **una playlist alla volta** per conservare nomi e separazione:

1. In Spotify apri la playlist e copia tutte le tracce come descritto sopra.
2. In PlaylistBridge usa l'importazione dagli appunti.
3. Inserisci o conferma il nome della playlist.
4. Controlla nell'app il conteggio e le prime righe riconosciute.
5. Ripeti per la playlist successiva creando una nuova sessione.

Per **Brani che ti piacciono**, apri quella sezione in Spotify, seleziona e copia tutte le
tracce, poi assegna nell'app un nome come `Brani che ti piacciono - Spotify`. Il risultato
sarà una normale playlist YouTube Music; le tracce non vengono automaticamente marcate con
"Mi piace".

Se gli appunti contengono soltanto identificativi Spotify, il recupero dei relativi
metadati può richiedere connessione Internet ed essere più lento. Non avviare il
trasferimento finché il riepilogo non mostra titoli e artisti plausibili.

## 4. Collegare YouTube Music in locale

PlaylistBridge usa l'autenticazione browser supportata da `ytmusicapi`. Questa procedura
non invia la sessione a PlaylistBridge o a un suo server: i dati vengono letti e usati sul
PC. `ytmusicapi` è però una libreria non ufficiale, quindi il metodo può cambiare con futuri
aggiornamenti di YouTube Music.

### Ottenere gli header dal browser

1. Apri `https://music.youtube.com` in Edge o Chrome e accedi all'account corretto.
2. Premi `F12` per aprire gli strumenti per sviluppatori e scegli **Network/Rete**.
3. Ricarica YouTube Music e, nel filtro della rete, cerca `browse`.
4. Seleziona una richiesta `POST` diretta a `music.youtube.com/youtubei/v1/browse`.
5. Nella sezione **Headers/Intestazioni**, copia le **Request Headers/Intestazioni della
   richiesta** in formato testuale seguendo l'indicazione mostrata nell'app.
6. Incolla il testo nella schermata di collegamento di PlaylistBridge e completa la verifica
   dell'account.

Gli header possono contenere cookie che equivalgono temporaneamente all'accesso al tuo
account Google. Sono quindi **segreti**:

- non inviarli via chat, email, GitHub o screenshot;
- non salvarli in file condivisi o sincronizzati;
- non inserirli in issue o log;
- se pensi di averli condivisi, esci dalle sessioni Google interessate e cambia la password
  se richiesto dalle indicazioni di sicurezza dell'account.

Chiudi gli strumenti per sviluppatori dopo il collegamento. Se l'autenticazione scade,
ripeti la procedura sullo stesso PC. Non usare header o cookie di un'altra persona.

## 5. Cercare e revisionare le corrispondenze

Dopo l'importazione, avvia la ricerca su YouTube Music. Per ogni traccia, controlla almeno:

- titolo e artista;
- durata approssimativa;
- etichette come `live`, `remix`, `karaoke`, `cover`, `sped up` o `instrumental`;
- contenuti espliciti e versioni remaster, quando sono importanti.

Conferma i match sicuri, scegli manualmente tra i candidati per quelli dubbi e lascia
saltati i brani senza un risultato affidabile. Un primo risultato di ricerca non garantisce
che sia la stessa registrazione.

## 6. Trasferire, interrompere e riprendere

1. Controlla il nome sorgente: PlaylistBridge aggiunge automaticamente data e dicitura
   `da Spotify`; la playlist di destinazione viene sempre creata come **privata**.
2. Controlla il riepilogo: confermati, da revisionare, saltati ed errori.
3. Avvia il trasferimento soltanto dopo aver risolto i match dubbi che ti interessano.
4. Lascia il PC connesso a Internet e non chiudere l'app durante una scrittura in corso.
5. In caso di interruzione, riapri la sessione JSON più recente con **Apri sessione** e
   premi di nuovo **Trasferisci playlist**: l'app riusa la playlist YouTube già creata e
   riparte dall'ultimo blocco confermato.

PlaylistBridge salva un checkpoint ogni 10 ricerche e dopo ciascun blocco di massimo 50
brani aggiunti. Prima di riprendere, confronta lo stato salvato con il contenuto reale della
playlist: anche se una risposta si perde dopo un timeout, non ripete un blocco già presente.
Se la playlist di destinazione è stata modificata manualmente nel frattempo, si ferma invece
di rischiare duplicati o un ordine errato.

## 7. Controllare il report

Alla fine, apri il report CSV prodotto automaticamente dalla build e conserva almeno:

- playlist sorgente, destinazione e relativo ID;
- titolo e artista originali;
- risultato YouTube Music selezionato;
- stato trasferito, saltato o errore;
- eventuale motivo dell'errore.

Confronta anche nell'app YouTube Music il numero di tracce e alcuni punti dell'ordine
(inizio, centro e fine). Il report non contiene cookie, header o token e può essere
rigenerato in qualsiasi momento con **Esporta report**.

## Fallback: Account data Spotify

Se la copia da Spotify Desktop non funziona, richiedi dal portale privacy di Spotify una
copia dei tuoi **Account data**. Spotify prepara un archivio da scaricare; i tempi dipendono
da Spotify. Conserva lo ZIP privatamente perché contiene dati personali.

L'importazione diretta di questo archivio è nella roadmap finché non è visibile nella GUI.
Nel frattempo non caricare l'archivio su convertitori casuali: attendi una build che esponga
l'importazione Account data oppure usa soltanto file estratti e verificati secondo le
istruzioni della relativa versione.

## Limiti e sicurezza

- PlaylistBridge non è affiliato a Spotify, Google o YouTube.
- Non rimuove né modifica le playlist Spotify e non deve eliminare playlist YouTube Music.
- Non aggira DRM e non trasferisce audio: ricrea playlist cercando brani disponibili nel
  catalogo di destinazione.
- Alcuni brani possono essere assenti, bloccati nella regione o disponibili solo in una
  versione differente.
- Prima di migrare migliaia di tracce, completa e verifica la prova da 10–20 brani.
