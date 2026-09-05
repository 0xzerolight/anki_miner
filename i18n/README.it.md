<!-- i18n-source: README.md sha256:7ca8f4a43c52af6c -->

<h1 align="center">
  <img src="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/anki_miner/gui/resources/icons/anki_miner.svg" height="76" align="absmiddle" alt=""> Anki Miner
</h1>

<p align="center">
<a href="https://pypi.org/project/anki-miner/"><img src="https://img.shields.io/pypi/v/anki-miner.svg" alt="PyPI version"></a>
<a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"></a>
<a href="https://www.gnu.org/licenses/gpl-3.0"><img src="https://img.shields.io/badge/License-GPLv3-blue.svg" alt="License: GPL v3"></a>
<a href="https://github.com/0xzerolight/anki_miner/releases/latest"><img src="https://img.shields.io/github/downloads/0xzerolight/anki_miner/total.svg" alt="GitHub downloads"></a>
<a href="https://github.com/0xzerolight/anki_miner/stargazers"><img src="https://img.shields.io/github/stars/0xzerolight/anki_miner?style=social" alt="GitHub stars"></a>
<a href="https://discord.com/invite/aDtQyZzUVP"><img src="https://img.shields.io/discord/1517634859110240326?logo=discord&logoColor=white&label=Discord&color=5865F2" alt="Discord community"></a>
</p>

<!-- i18n-nav:start -->
<p align="center">
<a href="../README.md">English</a> ·
<a href="README.ja.md">日本語</a> ·
<a href="README.ru.md">Русский</a> ·
<a href="README.fr.md">Français</a> ·
<a href="README.es.md">Español</a> ·
<a href="README.de.md">Deutsch</a> ·
<a href="README.pt_br.md">Português (Brasil)</a> ·
<a href="README.id.md">Bahasa Indonesia</a> ·
<a href="README.vi.md">Tiếng Việt</a> ·
<a href="README.zh_cn.md">简体中文</a> ·
<a href="README.zh_tw.md">繁體中文</a> ·
<b>Italiano</b>
</p>
<!-- i18n-nav:end -->

<p align="center">
Trasforma contenuti giapponesi, cinesi e coreani nativi in carte di vocabolario Anki.
</p>

<p align="center">
Anche su Android - <a href="https://github.com/0xzerolight/anki_miner_android">Anki Miner per Android</a>.
</p>

<p align="center">
Lascia una ⭐ stella se Anki Miner ti è stato utile - aiuta altri a trovarlo :).
</p>


# <p align="center">Demo del Mining</p>

![Anki Miner Showcase](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.gif)

<p align="center">⬇️ <a href="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.mp4">Demo completa con audio (MP4)</a></p>

### Esempi di carte

| ![ホント](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.gif) | ![いちゃいちゃ](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.gif) | ![代](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.gif) |
|:--:|:--:|:--:|
| ⬇️ [MP4 (audio)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.mp4) | ⬇️ [MP4 (audio)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.mp4) | ⬇️ [MP4 (audio)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.mp4) |

## Installazione

### Requisiti

- **Anki** con l'estensione [AnkiConnect](https://ankiweb.net/shared/info/2055492159) (codice `2055492159`)
- **ffmpeg** + **libmpv** (solo anteprima video) - necessario solo quando si installa tramite pip/pipx o codice sorgente.

Scarica il pacchetto per la tua piattaforma dall'[ultima versione](https://github.com/0xzerolight/anki_miner/releases/latest):

| Piattaforma | Download |
|----------|----------|
| Windows | `AnkiMiner-*-Setup.exe` |
| macOS (Apple Silicon / M1-M4) | `AnkiMiner-*-macOS-arm64.dmg` |
| macOS (Intel) | `AnkiMiner-*-macOS-x86_64.dmg` ¹ |
| Linux (Debian/Ubuntu) | `anki-miner_*_amd64.deb` |
| Linux (altro) | `AnkiMiner-*-Linux-x86_64.AppImage` |

¹ Esclude la generazione locale di sottotitoli con Whisper e le schermate AVIF. Per la piena funzionalità: `pipx install "anki-miner[asr]"`.

### Note sul primo avvio (build non firmate)

- **macOS**: apri il `.dmg`, trascina **AnkiMiner** su **Applicazioni** e avvialo. Il primo avvio viene bloccato perché l'app non è autenticata da Apple: chiudi la finestra, riapri l'app e scegli **Apri comunque** (anche in **Impostazioni di Sistema** -> **Privacy e sicurezza**). Solo la prima volta.
- **AppImage Linux**: rendilo eseguibile prima di avviarlo - `chmod +x AnkiMiner-*.AppImage`, oppure **Proprietà** -> **Consenti esecuzione** nel gestore file.
- **Windows SmartScreen**: **Ulteriori informazioni** -> **Esegui comunque**.
- **Falso positivo di Windows Defender**: ripristina da **Cronologia protezione** oppure [segnala a Microsoft](https://www.microsoft.com/en-us/wdsi/filesubmission).

<details>
<summary><strong>Installazione da PyPI (Python 3.11+)</strong></summary>

```bash
pipx install anki-miner   # or: pip install anki-miner
anki_miner_gui
```

Il giapponese non richiede nulla in più. Per il mining di cinese o coreano, aggiungi il motore:

```bash
pipx install "anki-miner[languages]"   # both; or [zh] / [ko] for one
```

I download qui sopra li recuperano direttamente nell'app, da Impostazioni -> Lingua di mining.

</details>

<details>
<summary><strong>Installazione dal codice sorgente</strong></summary>

```bash
git clone https://github.com/0xzerolight/anki_miner.git
cd anki_miner
pip install -e .
anki_miner_gui
```

Per la configurazione completa dell'ambiente di sviluppo, consulta [CONTRIBUTING.md](../CONTRIBUTING.md).

</details>

## Schede

- **Video** - estrai da una singola coppia video/sottotitoli, una cartella in batch o URL YouTube.
- **Costruttore di mazzi** - estrai un'intera serie in un unico mazzo ordinato per frequenza.
- **Audiolibri** - estrai audiolibri, podcast, radio, canzoni (coppie audio + sottotitoli/trascrizione).
- **Lettura** - estrai manga (mokuro), romanzi (`.epub`, `.txt`; un singolo libro o un'intera cartella), file di sottotitoli autonomi o testo incollato.
- **Analisi** - cronologia del mining, classifiche di difficoltà, traguardi.
- **Utilità** - genera sottotitoli (Whisper locale), risincronizza sottotitoli (ffsubsync/alass), condensa i contenuti multimediali in audio con solo dialoghi, scarica video/audio/sottotitoli da qualsiasi sito supportato da yt-dlp, copia la parte che vale la pena imparare di un mazzo predefinito in uno nuovo e completa retroattivamente i campi delle carte esistenti.
- **Impostazioni** - tutto ciò che è configurabile.

## Altre funzionalità

- Lingue di mining - giapponese, cinese e coreano, si cambiano nelle Impostazioni. Il coreano scarica il suo modello linguistico dall'app.
- Curatore di parole - rivedi ogni parola candidata prima che vengano create le carte, con la sua scena, pagina del manga e voce del dizionario affiancate.
- Annulla un'esecuzione - elimina le note appena create da un'esecuzione, direttamente dalla sua finestra dei risultati.
- Filtri avanzati: i+1, intervallo di rango di frequenza, blacklist, regex, insiemi di parole e altro ancora.
- Importazione offline di dizionari Yomitan - definizioni, accento tonale, frequenza - concatenati per priorità.
- Più elenchi di frequenza concatenati per priorità.
- Audio delle parole sulle carte da pacchetti audio locali, JapanesePod101 o Google TTS.
- Audio delle frasi sulle carte di Lettura da Google Translate TTS o Naver Papago (disattivato per impostazione predefinita).
- Stile del glossario per dizionario, in stile Yomitan.
- Anteprima video integrata con libmpv - riproduci la scena di una parola durante la revisione, oppure regola la sincronizzazione dei sottotitoli con la riproduzione dal vivo.
- Schermate animate (vedi gli esempi di carte sopra).
- Profili delle impostazioni - salva configurazioni con nome e passa dall'una all'altra dall'intestazione.
- Riapplica stile alle carte estratte - riapplica il tuo stile attuale delle carte a quelle già create (menu Strumenti).

<details>
<summary><strong>Temi integrati (29)</strong></summary>

- **Ayu** - Light, Mirage, Dark
- **Catppuccin** - Latte (light); Frappé, Macchiato, Mocha (dark)
- **Dracula** - Dracula, Alucard
- **Everforest** - Light, Dark
- **GitHub** - Light; Dark, Dark Dimmed
- **Gruvbox** - Light Medium, Dark Medium
- **Kanagawa** - Lotus (light), Wave (dark)
- **Rosé Pine** - Dawn (light); Main, Moon (dark)
- **Solarized** - Light, Dark
- **Standalone** - Light, Dark, Sakura, Nord, One Dark, Tokyo Night

Licenze dei temi: [LICENSE-THEMES.md](../LICENSE-THEMES.md). 
Vuoi che venga aggiunto un altro tema? Proponilo in una Issue di GitHub.

</details>

<details>
<summary><strong>Come funziona</strong></summary>

1. **Leggi i sottotitoli** e suddividi il testo in singole parole.
2. **Filtra** per ottenere le parole di contenuto che non conosci già - rivedendo facoltativamente l'elenco tu stesso nel Curatore di parole.
3. **Cattura una schermata e una clip audio** dal video per ogni riga.
4. **Cerca le definizioni** nei tuoi dizionari offline configurati, ricadendo facoltativamente su Jisho online se abilitato (più lento, con limite di velocità).
5. **Invia le carte finite ad Anki.**

</details>

## Risorse consigliate

Giapponese salvo diversa indicazione. La procedura guidata propone il set adatto alla tua lingua di mining.

| Tipo | Risorsa | Download | Aggiungi tramite |
|------|----------|----------|---------|
| Dizionario | [JMdict](https://github.com/yomidevs/jmdict-yomitan) | [ZIP Yomitan](https://github.com/yomidevs/jmdict-yomitan/releases/latest/download/JMdict_english.zip) | Aggiungi dizionario… |
| Dizionario | [Jitendex](https://jitendex.org/) | [ZIP Yomitan](https://github.com/stephenmk/stephenmk.github.io/releases/latest/download/jitendex-yomitan.zip) | Aggiungi dizionario… |
| Dizionario | [Bee's Character Dictionary](https://characterdictionary.tokyo/) | Generato sul sito | Aggiungi dizionario… |
| Accento tonale | [Kanjium](https://github.com/mifunetoshiro/kanjium) | [TSV](https://raw.githubusercontent.com/mifunetoshiro/kanjium/master/data/source_files/raw/accents.txt) | Accento tonale -> Aggiungi fonte di accento tonale… |
| Accento tonale | [アクセント辞典v2](https://learnjapanese.moe/yomichan/#dictionaries) | [Drive](https://drive.google.com/drive/folders/1tTdLppnqMfVC5otPlX_cs4ixlIgjv_lH) | Accento tonale -> Aggiungi fonte di accento tonale… |
| Frequenza | [JPDB v2.2 Kana](https://github.com/Kuuuube/yomitan-dictionaries) | [ZIP Yomitan](https://github.com/Kuuuube/yomitan-dictionaries/raw/main/dictionaries/JPDB_v2.2_Frequency_Kana_2024-10-13.zip) | Frequenza -> Aggiungi fonte di frequenza… |
| Frequenza | [BCCWJ SUW+LUW](https://github.com/Kuuuube/yomitan-dictionaries) | [ZIP Yomitan](https://github.com/Kuuuube/yomitan-dictionaries/raw/main/dictionaries/BCCWJ_SUW_LUW_combined.zip) | Frequenza -> Aggiungi fonte di frequenza… |
| Audio delle parole | [local-audio-yomichan](https://github.com/yomidevs/local-audio-yomichan) | Torrent della collezione o `android.db` generato | Audio -> Aggiungi fonte audio… |
| Dizionario (cinese) | [CC-CEDICT](https://github.com/MarvNC/cc-cedict-yomitan) | [Yomitan zip](https://github.com/MarvNC/cc-cedict-yomitan/releases/latest/download/CC-CEDICT.zip) | Aggiungi dizionario… |
| Dizionario (coreano) | [KRDICT](https://github.com/Lyroxide/yomitan-ko-dic) | [Yomitan zip](https://github.com/Lyroxide/yomitan-ko-dic/releases/latest/download/KO-EN.KRDICT.No.Examples.zip) | Aggiungi dizionario… |


<details>
<summary><strong>Licenza JMnedict</strong></summary>

Utilizza insiemi di nomi in bundle derivati da [JMnedict](https://www.edrdg.org/enamdict/enamdict_doc.html) (progetto JMdict/EDICT, EDRDG, CC BY-SA 4.0).

</details>

## Risoluzione dei problemi

| Problema                    | Soluzione                                                                         |
|--------------------------|----------------------------------------------------------------------------------|
| "Impossibile connettersi ad Anki" | Avvia Anki e assicurati che AnkiConnect sia installato.                                  |
| "Mazzo non trovato"         | Scegli un mazzo esistente in Impostazioni -> Carte e Anki. I mazzi non vengono creati automaticamente; creane uno in Anki prima se te ne serve uno nuovo. |
| "Tipo di nota non trovato"    | Configura i nomi dei campi del tuo tipo di nota in Impostazioni -> Carte e Anki.               |
| "ffmpeg non trovato"       | Installa ffmpeg e aggiungilo al PATH.                                               |
| Nessuna definizione trovata     | Aggiungi un dizionario Yomitan in Impostazioni -> Aggiungi dizionario… (consigliato), oppure abilita il fallback su Jisho (più lento, con limite di velocità). |
| L'installer di Windows non si apre / avviso SmartScreen | Consulta [Note sul primo avvio](#note-sul-primo-avvio-build-non-firmate): seleziona **Ulteriori informazioni** -> **Esegui comunque**; ripristina i falsi positivi di Defender da **Cronologia protezione**. |
| Un'installazione pulita non ha definizioni | Esegui Strumenti -> Procedura guidata di configurazione oppure Strumenti -> Scarica risorse consigliate. Per l'importazione manuale, mantieni intatto lo ZIP Yomitan (non estrarlo). |
| Aggiungi dizionario si blocca o fallisce | Annota l'ultima fase visibile e allega i log (vedi "Dove si trovano i log?" più sotto). Includi nella segnalazione il nome, la fonte e la dimensione dello ZIP del dizionario. |
| Dove si trovano i log?      | Usa Aiuto -> Apri cartella dei registri, oppure apri `%USERPROFILE%\.anki_miner\anki_miner.log` su Windows o `~/.anki_miner/anki_miner.log` su macOS/Linux. I log ruotati usano i suffissi da `.1` a `.5`. Invia anche `anki_miner.crash`, se c'è: un crash che ha chiuso l'app scrive lì il proprio stack anziché nel log, e `anki_miner.child.log`, che contiene l'output di un processo ausiliario. |
| Segnalare un bug          | Aiuto -> Esporta diagnostica… scrive uno ZIP in una posizione a tua scelta, con i log (`anki_miner.log` e le sue rotazioni, `anki_miner.crash`, `anki_miner.child.log`), il tuo `settings.json`, i file di configurazione e di stato dell'interfaccia, gli snapshot delle code e i manifest dei download, e alcuni report generati su macchina e stato dell'app (`environment.txt`, `health.txt`, `resources.txt`, `stores.txt`, `disk.txt`, `screens.txt`). Controllalo prima di caricarlo perché contiene percorsi e nomi di file del tuo computer. Nulla viene caricato automaticamente. |
| Più log diagnostici | Imposta `ANKI_MINER_LOG_LEVEL=DEBUG` prima di avviare Anki Miner per acquisire i dettagli di terze parti di yt-dlp, urllib3 e fugashi. Il valore predefinito è `WARNING`; i log di Anki Miner restano a DEBUG. |
| L'audio è nella lingua sbagliata  | Lo strumento prova prima le tracce audio nella lingua di mining, poi ricade su quella predefinita.      |
| Sottotitoli non sincronizzati    | Usa il controllo di offset dei sottotitoli nell'interfaccia grafica (intervallo ±300 secondi).                 |

## Roadmap

Elenco di idee per le versioni future di Anki Miner. Non in ordine di priorità. Le richieste di funzionalità hanno la precedenza.
- Suggerisci una funzionalità - [Apri una issue](https://github.com/0xzerolight/anki_miner/issues).
- Discuti della roadmap - [Discussioni](https://github.com/0xzerolight/anki_miner/discussions).

- **Funzionalità**:
  - [x] Selezione della lingua dell'interfaccia.
  - [x] Scheda di creazione sottotitoli locale: scheda opzionale per generare sottotitoli localmente.
  - [x] Scheda Lettura: estrai da manga e libri.
  - [x] Strumento di completamento delle carte.
  - [ ] Libreria multimediale: espandere la scheda Analisi per mostrare la libreria multimediale locale su tutti i formati.
  - [ ] Download automatico dei sottotitoli.

- **Lungo termine**:
  - [x] Port per Android -- https://github.com/0xzerolight/anki_miner_android
  - [x] Oltre il giapponese: mining di cinese e coreano.
  - [ ] Estensione per browser di Anki Miner.


## Come contribuire

Sono benvenuti contributi di qualsiasi tipo.
Se vuoi sostenere il progetto, condividilo con altri che potrebbero trarne beneficio.

- Sei nuovo? Inizia con [CONTRIBUTING.md](../CONTRIBUTING.md).
- Panoramica dell'architettura: [ARCHITECTURE.md](../ARCHITECTURE.md).
- Codice di condotta: [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md).
- Sicurezza: [SECURITY.md](../SECURITY.md).

Segnalazioni di bug e richieste di funzionalità -> [Issues](https://github.com/0xzerolight/anki_miner/issues).
Domande generali e discussioni -> [Discussioni](https://github.com/0xzerolight/anki_miner/discussions) o [Discord](https://discord.com/invite/aDtQyZzUVP).

## Ringraziamenti speciali

Un sincero ringraziamento alle persone che hanno dato un contributo eccezionale al progetto:

- ★ **[StyraxBenzoin](https://github.com/StyraxBenzoin)** - Suggerimenti di funzionalità brillanti, test delle nuove versioni, costruzione della community.
- ★ **[rob-olvr](https://github.com/rob-olvr)** - Eccellenti suggerimenti di funzionalità, costruzione della community e moderazione su Discord.

Consulta [CONTRIBUTORS.md](../CONTRIBUTORS.md) per l'elenco di chiunque abbia dato un contributo di qualsiasi tipo al progetto.


## Licenza

GNU General Public License v3.0. Consulta [LICENSE](../LICENSE).
