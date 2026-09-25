<!-- i18n-source: README.md sha256:eaf6f4a3bb1bf708 -->

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
<b>Français</b> ·
<a href="README.es.md">Español</a> ·
<a href="README.de.md">Deutsch</a> ·
<a href="README.pt_br.md">Português (Brasil)</a> ·
<a href="README.id.md">Bahasa Indonesia</a> ·
<a href="README.vi.md">Tiếng Việt</a> ·
<a href="README.zh_cn.md">简体中文</a> ·
<a href="README.zh_tw.md">繁體中文</a> ·
<a href="README.it.md">Italiano</a>
</p>
<!-- i18n-nav:end -->

<p align="center">
Transformez du contenu authentique en japonais, chinois, coréen et vingt-neuf autres langues en cartes de vocabulaire Anki.
</p>

<p align="center">
Aussi sur Android - <a href="https://github.com/0xzerolight/anki_miner_android">Anki Miner pour Android</a>.
</p>

<p align="center">
N'hésitez pas à laisser une ⭐ star si Anki Miner vous a aidé - cela aide d'autres personnes à le trouver :).
</p>


# <p align="center">Démo d'extraction</p>

![Anki Miner Showcase](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.gif)

<p align="center">⬇️ <a href="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.mp4">Démo complète avec son (MP4)</a></p>

### Exemples de cartes

| ![ホント](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.gif) | ![いちゃいちゃ](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.gif) | ![代](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.gif) |
|:--:|:--:|:--:|
| ⬇️ [MP4 (son)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.mp4) | ⬇️ [MP4 (son)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.mp4) | ⬇️ [MP4 (son)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.mp4) |

## Installation

### Prérequis

- **Anki** avec l'extension [AnkiConnect](https://ankiweb.net/shared/info/2055492159) (code `2055492159`)
- **ffmpeg** + **libmpv** (aperçu vidéo uniquement) - nécessaire seulement en cas d'installation via pip/pipx ou depuis les sources.

Téléchargez la version adaptée à votre plateforme depuis la [dernière version](https://github.com/0xzerolight/anki_miner/releases/latest) :

| Plateforme | Téléchargement |
|----------|----------|
| Windows | `AnkiMiner-*-Setup.exe` |
| macOS (Apple Silicon) | `AnkiMiner-*-macOS-arm64.dmg` |
| macOS (Intel) | `AnkiMiner-*-macOS-x86_64.dmg` ¹ |
| Linux (Debian/Ubuntu) | `anki-miner_*_amd64.deb` |
| Linux (autre) | `AnkiMiner-*-Linux-x86_64.AppImage` |

¹ Sans captures AVIF ni suppression des silences pour les sous-titres générés.

### Notes de premier lancement (versions non signées)

- **macOS** : ouvrez le `.dmg`, faites glisser **AnkiMiner** sur **Applications**, puis lancez-le. Le premier lancement est bloqué car l'application n'est pas notarisée : fermez la boîte de dialogue, rouvrez l'application et choisissez **Ouvrir quand même** (également dans **Réglages Système** -> **Confidentialité et sécurité**). Une seule fois.
- **AppImage Linux** : rendez le fichier exécutable avant de le lancer - `chmod +x AnkiMiner-*.AppImage`, ou **Propriétés** -> **Autoriser l'exécution** dans votre gestionnaire de fichiers.
- **SmartScreen de Windows** : **Plus d'informations** -> **Exécuter quand même**.
- **Faux positif de Windows Defender** : restaurez depuis l'**historique de protection** ou [signalez-le à Microsoft](https://www.microsoft.com/en-us/wdsi/filesubmission).

<details>
<summary><strong>Installer depuis PyPI (Python 3.11+)</strong></summary>

```bash
pipx install anki-miner   # or: pip install anki-miner
anki_miner_gui
```

Le japonais, l'indonésien et l'hébreu ne demandent rien de plus. Pour une autre langue d'extraction, ajoutez son moteur :

```bash
pipx install "anki-miner[languages]"   # all; or one of [zh], [ko], [yue], [en], [ca], [de], [pt], [fr], [es], [it], [nl], [nb], [ro], [el], [fi], [hu], [hr], [sv], [pl], [lt], [da], [ru], [uk], [sl], [tr], [th], [vi]
```

L'arabe et le persan n'ont pas d'extra. Leurs données, et les modèles spaCy des langues européennes, se téléchargent dans l'application via Paramètres -> Langue d'extraction, où les téléchargements ci-dessus récupèrent tout.

</details>

<details>
<summary><strong>Installer depuis les sources</strong></summary>

```bash
git clone https://github.com/0xzerolight/anki_miner.git
cd anki_miner
pip install -e .
anki_miner_gui
```

Pour une configuration de développement complète, consultez [CONTRIBUTING.md](../CONTRIBUTING.md).

</details>

## Onglets

- **Vidéo** - extraire une seule paire vidéo/sous-titres, un dossier de traitement par lot, ou des URL YouTube.
- **Livres audio** - extraire des livres audio, podcasts, radio, chansons (paires audio + sous-titres/transcription).
- **Lecture** - extraire des mangas (mokuro), des romans (`.epub`, `.txt` ; un seul livre ou un dossier entier), des fichiers de sous-titres autonomes, ou du texte collé.
- **Statistiques** - historique d'extraction, classements de difficulté, jalons.
- **Utilitaires** - générer des sous-titres (Whisper local), re-synchroniser les sous-titres (ffsubsync/alass), condenser les médias en audio dialogue uniquement, télécharger vidéo/audio/sous-titres depuis tout site pris en charge par yt-dlp, copier la partie utile d'un paquet préfait dans un nouveau, compléter les champs des cartes existantes, appliquer l'OCR aux images de pages de manga pour créer des fichiers .mokuro (mokuro), et caler un livre audio sur le texte de son livre (Synchronisation de livre audio).
- **Paramètres** - tout ce qui est configurable.

## Autres fonctionnalités

- Langues d'extraction - japonais, chinois, coréen, anglais, catalan, allemand, portugais, français, espagnol, italien, néerlandais, norvégien bokmål, roumain, grec, finnois, hongrois, croate, suédois, polonais, lituanien, danois, turc, indonésien, russe, arabe, thaï, persan, slovène, ukrainien, vietnamien, cantonais et hébreu, changées dans les Paramètres. Toutes les langues sauf le japonais téléchargent leur moteur dans l'application ; l'indonésien et l'hébreu n'en ont pas besoin.
- Curateur de mots - passez en revue chaque mot candidat avant la création des cartes, avec sa scène, sa page de manga et son entrée de dictionnaire côte à côte.
- Annuler une exécution - supprimez les notes qu'une exécution vient de créer, directement depuis sa boîte de dialogue de résultats.
- Filtrage étendu : i+1, plage de rang de fréquence, liste noire, regex, ensembles de mots, et plus encore.
- Import de dictionnaires Yomitan hors ligne - définitions, accent de hauteur, fréquence - chaînés par priorité.
- Plusieurs listes de fréquence chaînées par priorité.
- Audio de mot sur les cartes depuis des packs audio locaux, JapanesePod101, Google TTS ou Microsoft Edge TTS.
- Audio de phrase sur les cartes de Lecture depuis Google Translate TTS, ou Naver Papago pour le japonais et le coréen (désactivé par défaut).
- Style de glossaire par dictionnaire, à la manière de Yomitan.
- Aperçu vidéo libmpv intégré - lisez la scène d'un mot pendant la curation, ou ajustez le minutage des sous-titres avec une lecture en direct.
- Captures d'écran animées (voir les exemples de cartes ci-dessus).
- Profils de paramètres - enregistrez des configurations nommées et basculez entre elles depuis l'en-tête.
- Redéfinir le style des cartes extraites - réappliquez le style actuel de vos cartes aux cartes déjà créées (Utilitaires → Complétion des cartes).

<details>
<summary><strong>Thèmes intégrés (29)</strong></summary>

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

Licences des thèmes : [LICENSE-THEMES.md](../LICENSE-THEMES.md). 
Vous voulez qu'un autre thème soit ajouté ? Proposez-le dans une Issue GitHub.

</details>

<details>
<summary><strong>Comment ça marche</strong></summary>

1. **Lisez les sous-titres** et découpez le texte en mots individuels.
2. **Filtrez** pour ne garder que les mots pleins que vous ne connaissez pas déjà - en révisant éventuellement la liste vous-même dans le Curateur de mots.
3. **Récupérez une capture d'écran et un extrait audio** de la vidéo pour chaque ligne.
4. **Recherchez les définitions** dans vos dictionnaires hors ligne configurés, avec repli optionnel sur Jisho en ligne pour le japonais (plus lent, limité en débit).
5. **Envoyez les cartes terminées à Anki.**

</details>

## Ressources recommandées

Dictionnaires et listes de fréquence pour chaque langue d'extraction, avec les liens de téléchargement : [RESOURCES.md](../RESOURCES.md). L'assistant de configuration propose l'ensemble adapté à votre langue d'extraction.

<details>
<summary><strong>Licence JMnedict</strong></summary>

Utilise des ensembles de noms propres regroupés dérivés de [JMnedict](https://www.edrdg.org/enamdict/enamdict_doc.html) (projet JMdict/EDICT, EDRDG, CC BY-SA 4.0).

</details>

## Dépannage

| Problème                 | Solution                                                                         |
|--------------------------|----------------------------------------------------------------------------------|
| « Impossible de se connecter à Anki » | Démarrez Anki et assurez-vous qu'AnkiConnect est installé.                                  |
| « Paquet introuvable »         | Choisissez un paquet existant dans Paramètres -> Cartes et Anki. Les paquets ne sont pas créés automatiquement ; créez-en un dans Anki d'abord si vous en avez besoin d'un nouveau. |
| « Type de note introuvable »    | Configurez les noms de champs de votre type de note dans Paramètres -> Cartes et Anki.               |
| « ffmpeg introuvable »       | Installez ffmpeg et ajoutez-le au PATH.                                               |
| Aucune définition trouvée     | Ajoutez un dictionnaire Yomitan dans Paramètres -> Dictionnaires -> Ajouter un dictionnaire… (recommandé), ou, pour le japonais, activez le repli sur Jisho (plus lent, limité en débit). |
| L'installateur Windows ne s'ouvre pas / avertissement SmartScreen | Consultez les [Notes de premier lancement](#notes-de-premier-lancement-versions-non-signées) : sélectionnez **Plus d'informations** -> **Exécuter quand même** ; restaurez les faux positifs de Defender depuis l'**historique de protection**. |
| Une installation neuve n'a aucune définition | Exécutez Outils -> Assistant de configuration ou Outils -> Télécharger les ressources recommandées. Pour un import manuel, gardez l'archive ZIP Yomitan intacte (ne la décompressez pas). |
| L'ajout de dictionnaire se bloque ou échoue | Notez la dernière étape visible et joignez les journaux (voir « Où sont les journaux ? » ci-dessous). Indiquez le nom, la source et la taille de l'archive ZIP du dictionnaire dans le rapport. |
| Où sont les journaux ?      | Utilisez Aide -> Ouvrir le dossier des journaux, ou ouvrez `%USERPROFILE%\.anki_miner\anki_miner.log` sous Windows ou `~/.anki_miner/anki_miner.log` sous macOS/Linux. Les journaux archivés utilisent les suffixes `.1` à `.5`. Envoyez aussi `anki_miner.crash` s'il existe : un plantage qui a fait tomber l'application y écrit sa pile plutôt que dans le journal, ainsi que `anki_miner.child.log`, qui contient la sortie d'un processus auxiliaire. |
| Signaler un bug          | Aide -> Exporter les diagnostics… écrit une archive ZIP à l'emplacement de votre choix, contenant les journaux (`anki_miner.log` et ses rotations, `anki_miner.crash`, `anki_miner.child.log`), votre `settings.json`, vos fichiers de configuration et d'état de l'interface, les instantanés des files d'attente et les manifestes de téléchargement, ainsi que des rapports générés sur la machine et l'état de l'application (`environment.txt`, `health.txt`, `resources.txt`, `stores.txt`, `disk.txt`, `screens.txt`). Vérifiez-la avant de l'envoyer car elle contient des chemins et noms de fichiers de votre ordinateur. Rien n'est envoyé automatiquement. |
| Journalisation de diagnostic supplémentaire | Définissez `ANKI_MINER_LOG_LEVEL=DEBUG` avant de démarrer Anki Miner pour capturer les détails tiers de yt-dlp, urllib3 et fugashi. La valeur par défaut est `WARNING` ; les journaux d'Anki Miner restent en DEBUG. |
| L'audio est dans la mauvaise langue  | L'outil choisit la piste audio de la langue d'extraction, sinon la première. Choisissez-la vous-même avec Pistes (Vidéo -> Unique).      |
| Sous-titres désynchronisés    | Utilisez le réglage de décalage des sous-titres dans l'interface (plage ±300 secondes).                 |

## Feuille de route

Liste d'idées pour les futures versions d'Anki Miner. Sans ordre de priorité. Les demandes de fonctionnalités sont prioritaires.
- Suggérer une fonctionnalité - [Ouvrez une issue](https://github.com/0xzerolight/anki_miner/issues).
- Discuter de la feuille de route - [Discussions](https://github.com/0xzerolight/anki_miner/discussions).

- **Fonctionnalités** :
  - [x] Sélection de la langue de l'interface.
  - [x] Onglet de création locale de sous-titres : onglet facultatif pour générer des sous-titres localement.
  - [x] Onglet Lecture : extraire des mangas et des livres.
  - [x] Outil de complétion.
  - [ ] Bibliothèque multimédia : étendre l'onglet Statistiques pour afficher la bibliothèque multimédia locale pour tous les types de médias.
  - [ ] Téléchargement automatique de sous-titres.

- **Long terme** :
  - [x] Portage Android - https://github.com/0xzerolight/anki_miner_android
  - [x] Au-delà du japonais : trente et une autres langues d'extraction.
  - [ ] Extension de navigateur Anki Miner.


## Contribuer

Toute contribution est la bienvenue.
Si vous souhaitez soutenir le projet, merci de le partager avec d'autres personnes qui pourraient en bénéficier.

- Nouveau ici ? Commencez par [CONTRIBUTING.md](../CONTRIBUTING.md).
- Aperçu de l'architecture : [ARCHITECTURE.md](../ARCHITECTURE.md).
- Code de conduite : [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md).
- Sécurité : [SECURITY.md](../SECURITY.md).

Rapports de bugs et demandes de fonctionnalités -> [Issues](https://github.com/0xzerolight/anki_miner/issues).
Questions générales et discussions -> [Discussions](https://github.com/0xzerolight/anki_miner/discussions) ou [Discord](https://discord.com/invite/aDtQyZzUVP).

## Remerciements spéciaux

Sincères remerciements aux personnes qui ont apporté des contributions exceptionnelles au projet :

- ★ **[StyraxBenzoin](https://github.com/StyraxBenzoin)** - Suggestions de fonctionnalités brillantes, tests des nouvelles versions, construction de la communauté.
- ★ **[rob-olvr](https://github.com/rob-olvr)** - Excellentes suggestions de fonctionnalités, construction de communauté et modération sur Discord.

Consultez [CONTRIBUTORS.md](../CONTRIBUTORS.md) pour la liste de toutes les personnes ayant contribué au projet, de quelque manière que ce soit.


## Licence

Licence publique générale GNU v3.0. Voir [LICENSE](../LICENSE).
