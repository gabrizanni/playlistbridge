# PlaylistBridge

PlaylistBridge è un'app locale per Windows che aiuta a trasferire playlist da Spotify a
YouTube Music. Non scarica audio: legge i metadati dei brani, cerca le corrispondenze su
YouTube Music e crea playlist nell'account collegato.

> **Stato del progetto:** MVP funzionante da validare su Windows. Prima di usare una build su una
> libreria completa, provarla con una playlist privata di 10–20 brani e controllare il
> report.

## Principi

- esecuzione sul PC dell'utente, senza un server PlaylistBridge;
- nessun download o conversione di file audio;
- credenziali e dati di sessione esclusi dal repository e dagli artifact di build;
- revisione umana dei risultati incerti prima del trasferimento;
- uso di `ytmusicapi`, un client non ufficiale che potrebbe richiedere aggiornamenti se
  YouTube Music cambia il proprio sito.

## Avvio per sviluppatori

Richiede Python 3.12.

```powershell
git clone <URL-DEL-REPOSITORY>
cd playlistbridge
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
playlistbridge
```

In alternativa:

```powershell
python -m playlistbridge.app
```

Eseguire i controlli locali con:

```powershell
python -m pytest
python -m ruff check .
```

## Build Windows

Su Windows con Python 3.12:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1
```

Il risultato è `dist/PlaylistBridge-windows-x64.zip`. È una distribuzione **one-folder**:
estrarre tutto lo ZIP e avviare `PlaylistBridge.exe` dalla cartella estratta. Non spostare
soltanto il file `.exe`, perché dipende dagli altri file presenti accanto.

Il workflow GitHub Actions esegue test e lint, genera lo ZIP su `windows-latest` e lo carica
come artifact del workflow. Non crea release e non pubblica automaticamente il programma.

## Uso

La procedura operativa completa è in [docs/GUIDA_WINDOWS.md](docs/GUIDA_WINDOWS.md). In
sintesi: si importa una playlist alla volta, si collega localmente YouTube Music, si
revisionano le corrispondenze dubbie e infine si avvia il trasferimento.

## Roadmap dichiarata

- importazione di tutte le playlist da un singolo archivio "Account data" di Spotify;
- pacchetto firmato o installer Windows.

Il recupero dei metadati da URI, i checkpoint ogni 10 ricerche e ogni blocco di 50
inserimenti, la ripresa con verifica remota anti-duplicato e il report CSV sono già inclusi.
