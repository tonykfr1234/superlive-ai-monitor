<p align="center">
  <a href="README.md">English</a> · <a href="README.zh-CN.md">简体中文</a> · <a href="README.zh-TW.md">繁體中文</a> · <a href="README.ja.md">日本語</a> · <a href="README.ko.md">한국어</a><br>
  <a href="README.de.md">Deutsch</a> · <a href="README.es.md">Español</a> · <a href="README.fr.md">Français</a> · <a href="README.pt-BR.md">Português (Brasil)</a> · <a href="README.vi.md">Tiếng Việt</a> · <a href="README.id.md">Bahasa Indonesia</a> · <strong>Italiano</strong>
</p>

<p align="center"><img src="assets/superlive-ai.png" width="112" alt="SuperLive AI icon"></p>
<h1 align="center">Rilevamento del movimento di persone e veicoli tramite acquisizione delle telecamere di sorveglianza</h1>
<p align="center">SuperLive AI Monitor · Supporta gli emulatori Android MuMu e MEmu</p>
<p align="center"> <img src="https://img.shields.io/badge/platform-Windows-0078D6" alt="Windows"> <img src="https://img.shields.io/badge/Python-3.12-3776AB" alt="Python 3.12"> <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License"></a> </p>
<p align="center"><a href="#quick-start">Avvio rapido</a> · <a href="#workflow">Funzionamento</a> · <a href="#features">Funzioni</a> · <a href="#demo">Dimostrazione</a> · <a href="#faq">Domande frequenti</a> · <a href="#documentation">Documentazione</a></p>

SuperLive AI Monitor aggiunge la registrazione degli eventi all’immagine di SuperLive visibile in MuMu su Windows. Seleziona un’area poligonale: YOLO rileva persone e veicoli al suo interno e i video completati vengono inviati tramite Telegram. Non serve un indirizzo RTSP della telecamera.

È un progetto non ufficiale della comunità, senza affiliazione con i fornitori di SuperLive o MuMu. Le dodici lingue del README riguardano la documentazione; l’interfaccia del programma usa attualmente il cinese tradizionale.

**Aggiornamento del 21/10/2026:** Il movimento di persone o veicoli (auto, moto, autobus, camion) può avviare la registrazione. Restano la pausa di 120 secondi e i video da 120–300 secondi; i CSV giornalieri sono esenti. Telegram distingue persone, tipi di veicolo e targhe effettivamente lette, indicando quelle non confermate. L’OCR analizza l’immagine intera e zone sovrapposte, con ripristino automatico dopo un errore. Rotellina, barra di scorrimento e PageUp/PageDown mostrano fino a sette giorni di cronologia, comprese analisi dei video originali, letture assenti, candidati non confermati ed errori.

Per impostazione predefinita sono espansi solo i risultati confermati che corrispondono all’elenco privato con al massimo due modifiche di carattere; gli altri restano in una sezione richiudibile. Fai clic sul nome del video per selezionare l’originale in Esplora file di Windows.

Stato OCR, qualità e avanzamento del gruppo restano accanto ai comandi inferiori. Con il riconoscimento targhe attivo, le corrispondenze sono sempre visibili a destra, raggruppate per data di Taiwan con il conteggio giornaliero e le più recenti per prime. Nelle finestre strette l’elenco passa sotto l’immagine, che mantiene le proporzioni. “其他紀錄” espande gli altri risultati e porta a quella sezione; richiuderli riporta alle corrispondenze.


## Interfaccia a quattro riquadri

Video dal vivo in alto a sinistra, cronologia targhe a destra, ultima foto del veicolo in basso e comandi raggruppati. Trascina i titoli per scambiare i riquadri; L ripristina la disposizione. Esempio sintetico renderizzato con l’interfaccia reale.

![Dashboard diagram](docs/media/dashboard-guide.en.png)

[操作說明 / Guide](docs/DASHBOARD.en.md) · [排列動畫 / Layout animation](docs/media/dashboard-layout.gif)

**Installazione su un nuovo PC:** avvia `INSTALL.bat` nella cartella principale e configura ADB, Telegram e targhe nella finestra delle impostazioni. In basso a sinistra sono affiancati il veicolo e la targa ingrandita. [Guida in inglese](docs/INSTALL.en.md).

<a id="quick-start"></a>
## Avvio rapido

1. Installa MuMu e SuperLive, accedi e apri la vista in diretta da sorvegliare. Mantieni attiva la sessione di Windows.
2. Clona o scarica questo repository. Esegui `installer\INSTALL_FROM_REPO.bat` oppure il comando seguente.
3. Configura il tuo bot e la tua chat in locale con `installer\SET_TELEGRAM.bat`. Il repository non contiene credenziali.
4. Esegui `installer\VERIFY_ALL.bat` con la vista in diretta aperta.
5. Avvia `%USERPROFILE%\Downloads\SuperLive_AI\Start_AI_Monitor.bat`; usa `Stop_AI_Monitor.bat` per arrestare il sistema.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\installer\INSTALL_FROM_REPO.ps1
```

**È già installato?** Esegui `installer\UPDATE_FROM_REPO.bat`. L’aggiornamento crea una copia di sicurezza e arresta i programmi, conserva l’area e le impostazioni locali di Telegram e archiviazione, quindi lascia il monitoraggio fermo. Controlla l’immagine e riavvia manualmente. Non vengono creati avvii automatici di Windows, attività pianificate o servizi.

<a id="workflow"></a>
## Funzionamento

```text
Nuova attività di una persona o di un veicolo nel poligono
  → avviare la registrazione; bloccare nuovi invii automatici a Telegram per 120 secondi
  → continuare il rilevamento per tutta la registrazione
  → movimento dal secondo 120 e prima del 300: terminare il video e avviare subito il successivo
  → ogni nuovo video avvia un’altra pausa di invio di 120 secondi
  → terminare anche dopo 120 secondi senza movimento o al limite di 300 secondi
  → accodare il video completato per Telegram
  → continuare a sorvegliare; riprendere con un nuovo movimento o un nuovo ingresso
```

I video normali durano **da 120 a 300 secondi**. Una persona o un veicolo osservato per la prima volta all’avvio conta come nuova attività. Sia l’assenza sia l’immobilità contano come mancanza di movimento; ogni movimento rilevato fa ripartire il periodo di inattività. Dopo i primi 120 secondi, un movimento provoca anche il passaggio immediato al video successivo. Una persona che rimane ferma non avvia subito un altro video dopo la fine del precedente.

All’avvio non viene inviata alcuna foto su Telegram: gli invii automatici includono video completati, targhe confermate e rapporti CSV giornalieri. La pausa di invio di ogni nuovo video blocca anche i video precedenti e i relativi tentativi successivi. Cambi di video ripetuti possono ritardare l’intera coda. Vengono bloccate le nuove chiamate di invio; un trasferimento già avviato non può essere richiamato e potrebbe arrivare durante la pausa.

La selezione dell’area o l’assenza di risultati validi di acquisizione o rilevamento interrompe l’osservazione. Questo periodo sconosciuto non conta come inattività: al ripristino inizia un nuovo intervallo di osservazione.

<a id="features"></a>
## Funzioni

| Funzione | Comportamento |
| --- | --- |
| Area di rilevamento | Poligoni convessi e concavi; compatibilità con i rettangoli precedenti |
| Inclusione di persone e veicoli | Il riquadro dell’oggetto rilevato deve sovrapporsi all’area con una superficie positiva; il solo contatto con il bordo non basta |
| Immagine della telecamera | Esclude titolo e menu di MuMu; mantiene data, ora e nomi dei canali presenti nell’immagine |
| Registrazione | Normalmente 2–5 minuti; dal secondo 120, un movimento avvia subito il video successivo; termina anche dopo 120 secondi senza movimento |
| Avvisi e invio | Avvisi locali di Windows; video H.264 MP4 completati, targhe e rapporti CSV giornalieri su Telegram; tutti gli invii attendono nei primi 120 secondi di ogni video |
| Esecuzione e archiviazione | Supervisori separati e nuovi tentativi di acquisizione; valori predefiniti di 10 FPS e `C:\SuperLive_Recordings` |

Quando l’unità di registrazione raggiunge l’80 % di occupazione, vengono eliminati i video più vecchi idonei fino a scendere al 78 %. La registrazione attiva e i file protetti in attesa di invio vengono conservati. Il video contiene tutta l’immagine ritagliata della telecamera, senza assumere la forma del poligono.

<a id="controls"></a>
## Selezionare il poligono

| Tasto o azione | Effetto |
| --- | --- |
| R / Seleziona area | Iniziare una selezione |
| Clic sinistro | Aggiungere almeno tre vertici distinti seguendo il contorno |
| Clic destro / Backspace | Rimuovere l’ultimo vertice |
| Invio / Salva | Chiudere e salvare un poligono valido con lati che non si incrociano |
| Esc / Annulla | Mantenere l’area precedente |
| C / Immagine intera | Rimuovere la limitazione dell’area |
| Q / Arresta | Arrestare il monitoraggio e completare il video in corso |

Le scorciatoie richiedono che la finestra di anteprima sia attiva. Le coordinate vengono salvate rispetto al contenuto della telecamera. Se una vecchia area non può essere adattata con sicurezza al ritaglio, selezionala di nuovo oppure scegli esplicitamente l’immagine intera.

<a id="demo"></a>
## Dimostrazione

Questi esempi **senza audio di 36 secondi** usano il vero sistema di rendering dell’anteprima con una scena sintetica, riquadri delle persone predefiniti e stati di registrazione simulati. Le didascalie cinesi e inglesi mostrano la pausa di invio, il cambio di video e l’attesa di nuova attività. Non sono riprese reali né dimostrazioni dell’accuratezza di YOLO. Gli intervalli di 120 secondi sono abbreviati per la dimostrazione.

![Dimostrazione sintetica del poligono e della registrazione delle attività](docs/media/superlive-demo.gif)

[Apri o scarica la dimostrazione MP4](docs/media/superlive-demo.mp4)

| Selezionare l’area | Registrare l’attività | Attendere un nuovo movimento |
| --- | --- | --- |
| ![Selezione dei vertici](docs/media/polygon-selection.png) | ![Registrazione dell’attività](docs/media/activity-recording.png) | ![Monitoraggio dopo l’inattività](docs/media/inactivity-rearm.png) |

<a id="faq"></a>
## Domande frequenti

**Perché una persona ferma non viene registrata di nuovo?** Per avviare un altro video serve un nuovo movimento o una nuova persona che entra nell’area. All’avvio del programma, la prima osservazione conta come attività. Non viene inviato un avviso di inizio su Telegram.

**Come viene determinato il movimento?** Dai cambiamenti di posizione o dimensione del riquadro della persona o del veicolo. Le piccole oscillazioni vengono filtrate e gli spostamenti lenti si accumulano. Non è un’analisi della postura: i movimenti che non modificano il riquadro possono passare inosservati. Anche movimenti della telecamera, occlusioni ed errori di rilevamento possono influire sul risultato.

**Ogni video dura cinque minuti?** No. Normalmente dura da 2 a 5 minuti. Un movimento dal secondo 120 e prima del 300 termina il video e avvia subito il successivo; anche 120 secondi senza movimento lo terminano. Un arresto manuale o un guasto possono produrre un frammento più breve, che viene conservato localmente. Nessun invio inizia prima della fine della pausa ancora in vigore.

**Funziona in VMware e se Telegram non risponde?** È supportata l’inferenza su CPU; l’accelerazione grafica di VMware non fornisce CUDA. I video il cui invio fallisce restano nella coda e vengono ritentati.

<a id="documentation"></a>
## Documentazione e sicurezza

[Guida in inglese](docs/USER_GUIDE.md) · [Guida in cinese tradizionale](docs/USER_GUIDE.zh-TW.md) · [Architettura](docs/ARCHITECTURE.md) · [Progettazione della registrazione](docs/RECORDING.md) · [Novità](CHANGELOG.md) · [Sicurezza](SECURITY.md)

Il codice si trova in `src/`, i modelli pubblici di configurazione in `config/` e gli strumenti di installazione in `installer/`. Verifica la telecamera reale e l’invio Telegram nel tuo ambiente; i test offline non possono confermare questa configurazione.

Non pubblicare token reali, identificativi delle chat, impostazioni locali, registri, immagini della telecamera o copie di sicurezza private. Prima di inviare le modifiche, esegui `python scripts/security_scan.py .`.

## Licenza

[Licenza MIT](LICENSE).

## Daily CSV · 23:00 Asia/Taipei

Alle 23:00 (Taiwan) invia un CSV del giorno dalle 00:00 incluse alle 23:00 escluse e lo salva con i video. Il CSV giornaliero è esente dal silenzio della registrazione. Esclude gli eventi successivi e non invia supplementi a mezzanotte. Segnala anche zero eventi e riprova dopo errori o riavvii.
