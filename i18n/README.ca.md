<!-- i18n-source: README.md sha256:5600a42ee25483a5 -->

<h1 align="center">
  <img src="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/anki_miner/gui/resources/icons/anki_miner.svg" height="76" align="absmiddle" alt=""> Anki Miner
</h1>

<p align="center">
<a href="https://pypi.org/project/anki-miner/"><img src="https://img.shields.io/pypi/v/anki-miner.svg" alt="Versió a PyPI"></a>
<a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"></a>
<a href="https://www.gnu.org/licenses/gpl-3.0"><img src="https://img.shields.io/badge/License-GPLv3-blue.svg" alt="Llicència: GPL v3"></a>
<a href="https://github.com/0xzerolight/anki_miner/releases/latest"><img src="https://img.shields.io/github/downloads/0xzerolight/anki_miner/total.svg" alt="Baixades a GitHub"></a>
<a href="https://github.com/0xzerolight/anki_miner/stargazers"><img src="https://img.shields.io/github/stars/0xzerolight/anki_miner?style=social" alt="Estrelles a GitHub"></a>
<a href="https://discord.com/invite/aDtQyZzUVP"><img src="https://img.shields.io/discord/1517634859110240326?logo=discord&logoColor=white&label=Discord&color=5865F2" alt="Comunitat de Discord"></a>
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
<a href="README.it.md">Italiano</a> ·
<a href="README.ar.md">العربية</a> ·
<a href="README.yue.md">粵語</a> ·
<a href="README.sl.md">Slovenščina</a> ·
<a href="README.ko.md">한국어</a> ·
<a href="README.pt_pt.md">Português (Portugal)</a> ·
<a href="README.nl.md">Nederlands</a> ·
<a href="README.th.md">ไทย</a> ·
<a href="README.he.md">עברית</a> ·
<a href="README.fa.md">فارسی</a> ·
<b>Català</b> ·
<a href="README.nb.md">Norsk bokmål</a> ·
<a href="README.sv.md">Svenska</a> ·
<a href="README.da.md">Dansk</a>
</p>
<!-- i18n-nav:end -->

<p align="center">
Convertiu contingut original en japonès, xinès, coreà i vint-i-nou llengües més en targetes de vocabulari d'Anki.
</p>

<p align="center">
També a l'Android: <a href="https://github.com/0xzerolight/anki_miner_android">Anki Miner per a Android</a>.
</p>

<p align="center">
Si l'Anki Miner us ha estat útil, deixeu-hi una ⭐ estrella: ajuda altres persones a trobar-lo :).
</p>


# <p align="center">Demostració d'extracció</p>

![Mostra de l'Anki Miner](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.gif)

<p align="center">⬇️ <a href="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.mp4">Demostració completa amb so (MP4)</a></p>

### Targetes d'exemple

| ![ホント](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.gif) | ![いちゃいちゃ](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.gif) | ![代](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.gif) |
|:--:|:--:|:--:|
| ⬇️ [MP4 (amb so)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.mp4) | ⬇️ [MP4 (amb so)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.mp4) | ⬇️ [MP4 (amb so)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.mp4) |

## Instal·lació

### Requisits

- **Anki** amb el complement [AnkiConnect](https://ankiweb.net/shared/info/2055492159) (codi `2055492159`)
- **ffmpeg** + **libmpv** (només per a la previsualització de vídeo): només calen si s'instal·la amb pip/pipx o des del codi font.

Baixeu el fitxer per a la vostra plataforma de la [darrera versió](https://github.com/0xzerolight/anki_miner/releases/latest):

| Plataforma | Baixada |
|----------|----------|
| Windows | `AnkiMiner-*-Setup.exe` |
| macOS (Apple Silicon) | `AnkiMiner-*-macOS-arm64.dmg` |
| macOS (Intel) | `AnkiMiner-*-macOS-x86_64.dmg` ¹ |
| Linux (Debian/Ubuntu) | `anki-miner_*_amd64.deb` |
| Linux (altres) | `AnkiMiner-*-Linux-x86_64.AppImage` |

¹ No inclou les captures de pantalla AVIF ni l'eliminació de silencis per als subtítols generats.

### Notes per al primer inici (versions sense signar)

- **macOS**: obriu el `.dmg`, arrossegueu **AnkiMiner** a **Applications** i després inicieu-lo. El primer inici es bloqueja perquè l'aplicació no està notaritzada: tanqueu el diàleg, torneu a obrir l'aplicació i trieu **Open Anyway** (també a **System Settings** -> **Privacy & Security**). Només cal fer-ho un cop.
- **AppImage de Linux**: feu-lo executable abans d'executar-lo amb `chmod +x AnkiMiner-*.AppImage`, o amb **Propietats** -> **Permet executar** al gestor de fitxers.
- **SmartScreen del Windows**: **Més informació** -> **Executa igualment**.
- **Fals positiu del Windows Defender**: restaureu-lo des de l'**Historial de protecció** o [informeu-ne Microsoft](https://www.microsoft.com/en-us/wdsi/filesubmission).

<details>
<summary><strong>Instal·lació des de PyPI (Python 3.11+)</strong></summary>

```bash
pipx install anki-miner   # or: pip install anki-miner
anki_miner_gui
```

El japonès, l'indonesi i l'hebreu no necessiten res més. Per a una altra llengua d'extracció, afegiu-ne el motor:

```bash
pipx install "anki-miner[languages]"   # all; or one of [zh], [ko], [yue], [en], [ca], [de], [pt], [fr], [es], [it], [nl], [nb], [ro], [el], [fi], [hu], [hr], [sv], [pl], [lt], [da], [ru], [uk], [sl], [tr], [th], [vi]
```

L'àrab i el persa no tenen paquet addicional. Les seves dades, i els models de spaCy de les llengües europees, es baixen des de l'aplicació a Paràmetres -> Llengua d'extracció; les baixades de més amunt ja ho porten tot.

</details>

<details>
<summary><strong>Instal·lació des del codi font</strong></summary>

```bash
git clone https://github.com/0xzerolight/anki_miner.git
cd anki_miner
pip install -e .
anki_miner_gui
```

Per a la configuració de desenvolupament completa, vegeu [CONTRIBUTING.md](../CONTRIBUTING.md).

</details>

## Pestanyes

- **Vídeo**: extreu un parell de vídeo i subtítols, una carpeta per lots, URL de YouTube o Bilibili, o una sèrie sencera en una sola baralla ordenada per freqüència.
- **Audiollibres**: extreu audiollibres, pòdcasts, ràdio i cançons (parells d'àudio i subtítols o transcripció).
- **Lectura**: extreu manga (mokuro), novel·les (`.epub`, `.txt`; un sol llibre o una carpeta sencera), fitxers de subtítols independents o text enganxat.
- **Anàlisi**: historial d'extracció, classificacions de dificultat i fites.
- **Utilitats**: genera subtítols (Whisper local), ajusta el temps dels subtítols (ffsubsync/alass), condensa el contingut multimèdia en àudio només amb diàleg, baixa vídeo, àudio o subtítols de qualsevol lloc que admeti el yt-dlp, copia la part que val la pena aprendre d'una baralla prefeta en una de nova, completa camps de targetes existents, aplica OCR a imatges de pàgines de manga per crear fitxers .mokuro (mokuro) i sincronitza un audiollibre amb el text del seu llibre (Sincronia).
- **Paràmetres**: tot el que es pot configurar.

## Altres funcions

- Llengües d'extracció: japonès, xinès, coreà, anglès, català, alemany, portuguès, francès, castellà, italià, neerlandès, noruec bokmål, romanès, grec, finès, hongarès, croat, suec, polonès, lituà, danès, turc, indonesi, rus, àrab, tai, persa, eslovè, ucraïnès, vietnamita, cantonès i hebreu, que es canvien a Paràmetres. Les llengües que no són el japonès baixen el seu motor des de l'aplicació; l'indonesi i l'hebreu no en necessiten cap.
- Curador de paraules: reviseu cada paraula candidata abans de crear les targetes, amb la seva escena, la pàgina del manga i l'entrada del diccionari una al costat de l'altra.
- Desfer una execució: suprimiu les notes que acaba de crear una execució, directament des del seu diàleg de resultats.
- Filtratge extens: i+1, interval de posicions de freqüència, llista negra, expressions regulars, conjunts de paraules i més.
- Importació de diccionaris Yomitan fora de línia (definicions, accent tonal, freqüència), encadenats per prioritat.
- Diverses llistes de freqüència encadenades per prioritat.
- Àudio de paraules a les targetes des de paquets d'àudio locals, JapanesePod101, Google TTS o Microsoft Edge TTS.
- Àudio de frases a les targetes de Lectura des de Google Translate TTS, o Naver Papago per al japonès i el coreà (desactivat per defecte).
- Estil del glossari per diccionari, a l'estil de Yomitan.
- Previsualització de vídeo amb libmpv integrat: reproduïu l'escena d'una paraula mentre la reviseu o ajusteu la sincronia dels subtítols amb reproducció en directe.
- Captures de pantalla animades (vegeu les targetes d'exemple de més amunt).
- Perfils de paràmetres: deseu configuracions amb nom i canvieu-ne des de la capçalera.
- Reaplica l'estil a les targetes extretes: torneu a aplicar l'estil de targeta actual a les targetes que ja heu creat (Utilitats → Completa targetes).

<details>
<summary><strong>Temes integrats (29)</strong></summary>

- **Ayu**: Light, Mirage, Dark
- **Catppuccin**: Latte (clar); Frappé, Macchiato, Mocha (fosc)
- **Dracula**: Dracula, Alucard
- **Everforest**: Light, Dark
- **GitHub**: Light; Dark, Dark Dimmed
- **Gruvbox**: Light Medium, Dark Medium
- **Kanagawa**: Lotus (clar), Wave (fosc)
- **Rosé Pine**: Dawn (clar); Main, Moon (fosc)
- **Solarized**: Light, Dark
- **Independents**: Light, Dark, Sakura, Nord, One Dark, Tokyo Night

Llicències dels temes: [LICENSE-THEMES.md](../LICENSE-THEMES.md). 
Voleu que s'afegeixi un altre tema? Suggeriu-lo en una incidència de GitHub.

</details>

<details>
<summary><strong>Com funciona</strong></summary>

1. **Llegeix els subtítols** i divideix el text en paraules individuals.
2. **Filtra** les paraules de contingut que encara no coneixeu; opcionalment, podeu revisar la llista vosaltres mateixos al Curador de paraules.
3. **Pren una captura de pantalla i un clip d'àudio** del vídeo per a cada línia.
4. **Cerca les definicions** als diccionaris fora de línia que heu configurat.
5. **Envia les targetes acabades a l'Anki.**

</details>

## Recursos recomanats

Diccionaris i llistes de freqüència per a cada llengua d'extracció, amb enllaços de baixada: [RESOURCES.md](../RESOURCES.md). L'assistent de configuració ofereix el conjunt adequat per a la vostra llengua d'extracció.

<details>
<summary><strong>Llicència del JMnedict</strong></summary>

Utilitza conjunts de noms inclosos derivats del [JMnedict](https://www.edrdg.org/enamdict/enamdict_doc.html) (projecte JMdict/EDICT, EDRDG, CC BY-SA 4.0).

</details>

## Resolució de problemes

| Problema                 | Solució                                                                          |
|--------------------------|----------------------------------------------------------------------------------|
| «No es pot connectar a l'Anki» | Inicieu l'Anki i assegureu-vos que l'AnkiConnect està instal·lat.          |
| «No s'ha trobat la baralla» | Trieu una baralla existent a Paràmetres -> Targetes i Anki. Les baralles no es creen automàticament; si en necessiteu una de nova, creeu-la primer a l'Anki. |
| «No s'ha trobat el tipus de nota» | Configureu els noms dels camps del tipus de nota a Paràmetres -> Targetes i Anki. |
| «No s'ha trobat el ffmpeg» | Instal·leu el ffmpeg i afegiu-lo al PATH.                                      |
| No es troben definicions | Afegiu un diccionari Yomitan a Paràmetres -> Diccionaris -> Afegeix un diccionari… |
| L'instal·lador del Windows no s'obre / avís de SmartScreen | Vegeu les [notes per al primer inici](#notes-per-al-primer-inici-versions-sense-signar): seleccioneu **Més informació** -> **Executa igualment**; restaureu els falsos positius del Defender des de l'**Historial de protecció**. |
| Una instal·lació nova no té definicions | Executeu Eines -> Assistent de configuració o Eines -> Baixa els recursos recomanats. Per a la importació manual, mantingueu intacte el ZIP de Yomitan (no el descomprimiu). |
| Afegir un diccionari s'encalla o falla | Anoteu la darrera etapa visible i adjunteu els registres (vegeu «On són els registres?» més avall). Indiqueu a l'informe el nom, l'origen i la mida del ZIP del diccionari. |
| On són els registres?    | Utilitzeu Ajuda -> Obre la carpeta de registres, o obriu `%USERPROFILE%\.anki_miner\anki_miner.log` al Windows o `~/.anki_miner/anki_miner.log` al macOS/Linux. Els registres rotats utilitzen els sufixos `.1` a `.5`. Envieu també `anki_miner.crash` si hi és (una fallada que ha tancat l'aplicació escriu la pila en aquest fitxer, no al registre) i `anki_miner.child.log`, que conté la sortida d'un procés auxiliar. |
| Informar d'un error      | Ajuda -> Exporta el diagnòstic… escriu un ZIP a la ubicació que trieu, amb els registres (`anki_miner.log` i les seves rotacions, `anki_miner.crash`, `anki_miner.child.log`), el vostre `settings.json`, els fitxers de configuració i d'estat de la interfície, instantànies de les cues i manifests de baixades, i informes generats de l'estat de l'equip i de l'aplicació (`environment.txt`, `health.txt`, `resources.txt`, `stores.txt`, `disk.txt`, `screens.txt`). Reviseu-lo abans de pujar-lo, perquè conté camins i noms de fitxers del vostre ordinador. No es puja res automàticament. |
| Més registre de diagnòstic | Definiu `ANKI_MINER_LOG_LEVEL=DEBUG` abans d'iniciar l'Anki Miner per capturar els detalls de tercers del yt-dlp, l'urllib3 i el fugashi. El valor per defecte és `WARNING`; els registres de l'Anki Miner es mantenen a DEBUG. |
| L'àudio és en una altra llengua | L'eina tria la pista d'àudio de la llengua d'extracció i, si no n'hi ha, la primera. Trieu-la vosaltres mateixos amb Pista d'àudio… (Vídeo -> Individual). |
| Subtítols desincronitzats | Utilitzeu el control de desfasament dels subtítols de la interfície (interval de ±300 segons). |

## Contribucions

Les contribucions de qualsevol mena són benvingudes.
Si voleu donar suport al projecte, compartiu-lo amb altres persones a qui pugui ser útil.

- Sou nous aquí? Comenceu per [CONTRIBUTING.md](../CONTRIBUTING.md).
- Visió general de l'arquitectura: [ARCHITECTURE.md](../ARCHITECTURE.md).
- Codi de conducta: [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md).
- Seguretat: [SECURITY.md](../SECURITY.md).

Informes d'errors i sol·licituds de funcions -> [Issues](https://github.com/0xzerolight/anki_miner/issues).
Preguntes generals i debat -> [Discussions](https://github.com/0xzerolight/anki_miner/discussions) o [Discord](https://discord.com/invite/aDtQyZzUVP).

## Agraïments especials

Moltes gràcies a les persones que han fet contribucions excepcionals al projecte:

- ★ **[StyraxBenzoin](https://github.com/StyraxBenzoin)**: suggeriments de funcions brillants, proves de versions noves i creació de comunitat.
- ★ **[rob-olvr](https://github.com/rob-olvr)**: suggeriments de funcions excel·lents, creació de comunitat i moderació al Discord.

Vegeu [CONTRIBUTORS.md](../CONTRIBUTORS.md) per a totes les persones que han contribuït d'alguna manera al projecte.


## Llicència

GNU General Public License v3.0. Vegeu [LICENSE](../LICENSE).
