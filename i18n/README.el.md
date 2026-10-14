<!-- i18n-source: README.md sha256:570dfcaa73bf51ed -->

<h1 align="center">
  <img src="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/anki_miner/gui/resources/icons/anki_miner.svg" height="76" align="absmiddle" alt=""> Anki Miner
</h1>

<p align="center">
<a href="https://pypi.org/project/anki-miner/"><img src="https://img.shields.io/pypi/v/anki-miner.svg" alt="Έκδοση PyPI"></a>
<a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"></a>
<a href="https://www.gnu.org/licenses/gpl-3.0"><img src="https://img.shields.io/badge/License-GPLv3-blue.svg" alt="Άδεια: GPL v3"></a>
<a href="https://github.com/0xzerolight/anki_miner/releases/latest"><img src="https://img.shields.io/github/downloads/0xzerolight/anki_miner/total.svg" alt="Λήψεις στο GitHub"></a>
<a href="https://github.com/0xzerolight/anki_miner/stargazers"><img src="https://img.shields.io/github/stars/0xzerolight/anki_miner?style=social" alt="Αστέρια στο GitHub"></a>
<a href="https://discord.com/invite/aDtQyZzUVP"><img src="https://img.shields.io/discord/1517634859110240326?logo=discord&logoColor=white&label=Discord&color=5865F2" alt="Κοινότητα Discord"></a>
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
<a href="README.ca.md">Català</a> ·
<a href="README.nb.md">Norsk bokmål</a> ·
<a href="README.sv.md">Svenska</a> ·
<a href="README.da.md">Dansk</a> ·
<a href="README.pl.md">Polski</a> ·
<a href="README.uk.md">Українська</a> ·
<a href="README.hr.md">Hrvatski</a> ·
<a href="README.lt.md">Lietuvių</a> ·
<a href="README.fi.md">Suomi</a> ·
<a href="README.hu.md">Magyar</a> ·
<b>Ελληνικά</b>
</p>
<!-- i18n-nav:end -->

<p align="center">
Μετατρέψτε αυθεντικό περιεχόμενο στα ιαπωνικά, τα κινεζικά, τα κορεατικά και σε άλλες είκοσι εννέα γλώσσες σε κάρτες λεξιλογίου Anki.
</p>

<p align="center">
Διαθέσιμο και για Android - <a href="https://github.com/0xzerolight/anki_miner_android">Anki Miner για Android</a>.
</p>

<p align="center">
Αν το Anki Miner σάς βοήθησε, αφήστε ένα ⭐ αστέρι - έτσι το βρίσκουν κι άλλοι :).
</p>


# <p align="center">Επίδειξη εξόρυξης</p>

![Παρουσίαση του Anki Miner](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.gif)

<p align="center">⬇️ <a href="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.mp4">Πλήρης επίδειξη με ήχο (MP4)</a></p>

### Παραδείγματα καρτών

| ![ホント](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.gif) | ![いちゃいちゃ](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.gif) | ![代](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.gif) |
|:--:|:--:|:--:|
| ⬇️ [MP4 (με ήχο)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.mp4) | ⬇️ [MP4 (με ήχο)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.mp4) | ⬇️ [MP4 (με ήχο)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.mp4) |

## Εγκατάσταση

### Απαιτήσεις

- **Anki** με το πρόσθετο [AnkiConnect](https://ankiweb.net/shared/info/2055492159) (κωδικός `2055492159`)
- **ffmpeg** + **libmpv** (μόνο για την προεπισκόπηση βίντεο) - χρειάζονται μόνο σε εγκατάσταση μέσω pip/pipx ή από τον πηγαίο κώδικα.

Κατεβάστε το αρχείο για την πλατφόρμα σας από την [τελευταία έκδοση](https://github.com/0xzerolight/anki_miner/releases/latest):

| Πλατφόρμα | Λήψη |
|----------|----------|
| Windows | `AnkiMiner-*-Setup.exe` |
| macOS (Apple Silicon) | `AnkiMiner-*-macOS-arm64.dmg` |
| macOS (Intel) | `AnkiMiner-*-macOS-x86_64.dmg` ¹ |
| Linux (Debian/Ubuntu) | `anki-miner_*_amd64.deb` |
| Linux (άλλες διανομές) | `AnkiMiner-*-Linux-x86_64.AppImage` |

¹ Χωρίς στιγμιότυπα AVIF και χωρίς αφαίρεση σιωπής για τους υπότιτλους που δημιουργούνται.

### Σημειώσεις πρώτης εκτέλεσης (μη υπογεγραμμένες εκδόσεις)

- **macOS**: ανοίξτε το `.dmg`, σύρετε το **AnkiMiner** στις **Εφαρμογές** και εκκινήστε το. Η πρώτη εκκίνηση αποκλείεται επειδή η εφαρμογή δεν είναι επικυρωμένη (notarized): κλείστε το παράθυρο διαλόγου, ανοίξτε ξανά την εφαρμογή και επιλέξτε **Άνοιγμα ούτως ή άλλως** (υπάρχει επίσης στις **Ρυθμίσεις συστήματος** -> **Απόρρητο και ασφάλεια**). Μόνο μία φορά.
- **Linux AppImage**: κάντε το εκτελέσιμο πριν το εκτελέσετε - `chmod +x AnkiMiner-*.AppImage` ή **Ιδιότητες** -> **Να επιτρέπεται η εκτέλεση** στη διαχείριση αρχείων σας.
- **Windows SmartScreen**: **Περισσότερες πληροφορίες** -> **Εκτέλεση οπωσδήποτε**.
- **Ψευδώς θετικό του Windows Defender**: κάντε επαναφορά από το **Ιστορικό προστασίας** ή [αναφέρετέ το στη Microsoft](https://www.microsoft.com/en-us/wdsi/filesubmission).

<details>
<summary><strong>Εγκατάσταση από το PyPI (Python 3.11+)</strong></summary>

```bash
pipx install anki-miner   # or: pip install anki-miner
anki_miner_gui
```

Τα ιαπωνικά, τα ινδονησιακά και τα εβραϊκά δεν χρειάζονται τίποτα επιπλέον. Για άλλη γλώσσα εξόρυξης, προσθέστε τη μηχανή της:

```bash
pipx install "anki-miner[languages]"   # all; or one of [zh], [ko], [yue], [en], [ca], [de], [pt], [fr], [es], [it], [nl], [nb], [ro], [el], [fi], [hu], [hr], [sv], [pl], [lt], [da], [ru], [uk], [sl], [tr], [th], [vi]
```

Τα αραβικά και τα περσικά δεν έχουν επιπλέον πακέτο. Τα δεδομένα τους, καθώς και τα μοντέλα spaCy των ευρωπαϊκών γλωσσών, λαμβάνονται μέσα από την εφαρμογή στις Ρυθμίσεις -> Γλώσσα εξόρυξης, όπου οι λήψεις φέρνουν όλα όσα αναφέρονται παραπάνω.

</details>

<details>
<summary><strong>Εγκατάσταση από τον πηγαίο κώδικα</strong></summary>

```bash
git clone https://github.com/0xzerolight/anki_miner.git
cd anki_miner
pip install -e .
anki_miner_gui
```

Για πλήρη ρύθμιση περιβάλλοντος ανάπτυξης, δείτε το [CONTRIBUTING.md](../CONTRIBUTING.md).

</details>

## Καρτέλες

- **Βίντεο** - εξόρυξη από ένα ζεύγος βίντεο/υποτίτλων, έναν φάκελο μαζικά, URL του YouTube ή του Bilibili ή μια ολόκληρη σειρά σε μία τράπουλα ταξινομημένη κατά συχνότητα.
- **Ηχοβιβλία** - εξόρυξη από ηχοβιβλία, podcast, ραδιόφωνο, τραγούδια (ζεύγη ήχου + υποτίτλων/απομαγνητοφώνησης).
- **Ανάγνωση** - εξόρυξη από manga (mokuro), μυθιστορήματα (`.epub`, `.txt`· ένα βιβλίο ή ολόκληρο φάκελο), μεμονωμένα αρχεία υποτίτλων ή επικολλημένο κείμενο.
- **Στατιστικά** - ιστορικό εξόρυξης, κατατάξεις δυσκολίας, ορόσημα.
- **Βοηθήματα** - δημιουργία υποτίτλων (τοπικό Whisper), χρονισμός υποτίτλων (ffsubsync/alass), συμπύκνωση πολυμέσων σε ήχο μόνο με διαλόγους, λήψη βίντεο/ήχου/υποτίτλων από κάθε ιστότοπο που υποστηρίζει το yt-dlp, αντιγραφή του χρήσιμου για μάθηση μέρους μιας έτοιμης τράπουλας σε νέα, συμπλήρωση πεδίων σε υπάρχουσες κάρτες, OCR εικόνων σελίδων manga σε αρχεία .mokuro (mokuro) και χρονισμός ενός ηχοβιβλίου με το κείμενο του βιβλίου του (Συγχρονισμός).
- **Ρυθμίσεις** - όλα όσα μπορούν να ρυθμιστούν.

## Άλλες λειτουργίες

- Γλώσσες εξόρυξης - ιαπωνικά, κινεζικά, κορεατικά, αγγλικά, καταλανικά, γερμανικά, πορτογαλικά, γαλλικά, ισπανικά, ιταλικά, ολλανδικά, νορβηγικά (Bokmål), ρουμανικά, ελληνικά, φινλανδικά, ουγγρικά, κροατικά, σουηδικά, πολωνικά, λιθουανικά, δανικά, τουρκικά, ινδονησιακά, ρωσικά, αραβικά, ταϊλανδικά, περσικά, σλοβενικά, ουκρανικά, βιετναμικά, καντονέζικα και εβραϊκά, με αλλαγή στις Ρυθμίσεις. Οι γλώσσες εκτός από τα ιαπωνικά κατεβάζουν τη μηχανή τους μέσα από την εφαρμογή· τα ινδονησιακά και τα εβραϊκά δεν χρειάζονται.
- Επιμελητής λέξεων - ελέγξτε κάθε υποψήφια λέξη πριν δημιουργηθούν οι κάρτες, με τη σκηνή της, τη σελίδα του manga και το λήμμα του λεξικού δίπλα δίπλα.
- Αναίρεση εκτέλεσης - διαγραφή των σημειώσεων που μόλις δημιούργησε μια εκτέλεση, κατευθείαν από το παράθυρο αποτελεσμάτων της.
- Εκτεταμένο φιλτράρισμα: i+1, εύρος κατάταξης συχνότητας, μαύρη λίστα, regex, σύνολα λέξεων και άλλα.
- Εισαγωγή λεξικών Yomitan εκτός σύνδεσης - ορισμοί, μουσικός τονισμός, συχνότητα - σε αλυσίδα κατά προτεραιότητα.
- Πολλαπλές λίστες συχνότητας σε αλυσίδα κατά προτεραιότητα.
- Ήχος λέξεων στις κάρτες από τοπικά πακέτα ήχου, JapanesePod101, Google TTS ή Microsoft Edge TTS.
- Ήχος πρότασης στις κάρτες Ανάγνωσης από Google Translate TTS ή Naver Papago για ιαπωνικά και κορεατικά (απενεργοποιημένο από προεπιλογή).
- Μορφοποίηση ορισμών ανά λεξικό, σε στιλ Yomitan.
- Ενσωματωμένη προεπισκόπηση βίντεο libmpv - αναπαραγωγή της σκηνής μιας λέξης κατά την επιμέλεια ή μικρορύθμιση του χρονισμού υποτίτλων με ζωντανή αναπαραγωγή.
- Κινούμενα στιγμιότυπα οθόνης (δείτε τα παραδείγματα καρτών παραπάνω).
- Προφίλ ρυθμίσεων - αποθήκευση επώνυμων διαμορφώσεων και εναλλαγή μεταξύ τους από την κεφαλίδα.
- Νέα μορφοποίηση καρτών εξόρυξης - εφαρμόζει ξανά την τρέχουσα μορφοποίηση καρτών σε κάρτες που έχετε ήδη δημιουργήσει (Βοηθήματα → Συμπλήρωση).

<details>
<summary><strong>Ενσωματωμένα θέματα (29)</strong></summary>

- **Ayu** - Light, Mirage, Dark
- **Catppuccin** - Latte (φωτεινό)· Frappé, Macchiato, Mocha (σκούρα)
- **Dracula** - Dracula, Alucard
- **Everforest** - Light, Dark
- **GitHub** - Light· Dark, Dark Dimmed
- **Gruvbox** - Light Medium, Dark Medium
- **Kanagawa** - Lotus (φωτεινό), Wave (σκούρο)
- **Rosé Pine** - Dawn (φωτεινό)· Main, Moon (σκούρα)
- **Solarized** - Light, Dark
- **Αυτόνομα** - Light, Dark, Sakura, Nord, One Dark, Tokyo Night

Άδειες θεμάτων: [LICENSE-THEMES.md](../LICENSE-THEMES.md). 
Θέλετε να προστεθεί κάποιο άλλο θέμα; Προτείνετέ το σε ένα GitHub Issue.

</details>

<details>
<summary><strong>Πώς λειτουργεί</strong></summary>

1. **Διαβάζει τους υπότιτλους** και χωρίζει το κείμενο σε μεμονωμένες λέξεις.
2. **Φιλτράρει** ώστε να μείνουν οι λέξεις περιεχομένου που δεν γνωρίζετε ήδη - προαιρετικά ελέγχετε εσείς τη λίστα στον Επιμελητή λέξεων.
3. **Παίρνει στιγμιότυπο οθόνης και κλιπ ήχου** από το βίντεο για κάθε γραμμή.
4. **Αναζητά ορισμούς** στα λεξικά εκτός σύνδεσης που έχετε ρυθμίσει.
5. **Στέλνει τις έτοιμες κάρτες στο Anki.**

</details>

## Προτεινόμενοι πόροι

Λεξικά και λίστες συχνότητας για κάθε γλώσσα εξόρυξης, με συνδέσμους λήψης: [RESOURCES.md](../RESOURCES.md). Ο Οδηγός ρύθμισης προτείνει το κατάλληλο σύνολο για τη γλώσσα εξόρυξής σας.

<details>
<summary><strong>Άδεια JMnedict</strong></summary>

Χρησιμοποιεί ενσωματωμένα σύνολα ονομάτων που προέρχονται από το [JMnedict](https://www.edrdg.org/enamdict/enamdict_doc.html) (έργο JMdict/EDICT, EDRDG, CC BY-SA 4.0).

</details>

## Αντιμετώπιση προβλημάτων

| Πρόβλημα                 | Λύση                                                                             |
|--------------------------|----------------------------------------------------------------------------------|
| «Cannot connect to Anki» | Ξεκινήστε το Anki και βεβαιωθείτε ότι το AnkiConnect είναι εγκατεστημένο.        |
| «Deck not found»         | Επιλέξτε μια υπάρχουσα τράπουλα στις Ρυθμίσεις -> Κάρτες & Anki. Οι τράπουλες δεν δημιουργούνται αυτόματα· αν χρειάζεστε νέα, δημιουργήστε την πρώτα στο Anki. |
| «Note type not found»    | Ρυθμίστε τα ονόματα πεδίων του τύπου σημείωσής σας στις Ρυθμίσεις -> Κάρτες & Anki. |
| «ffmpeg not found»       | Εγκαταστήστε το ffmpeg και προσθέστε το στο PATH.                                |
| Δεν βρέθηκαν ορισμοί     | Προσθέστε ένα λεξικό Yomitan στις Ρυθμίσεις -> Λεξικά -> Προσθήκη λεξικού… |
| Το πρόγραμμα εγκατάστασης των Windows δεν ανοίγει / προειδοποίηση SmartScreen | Δείτε τις [Σημειώσεις πρώτης εκτέλεσης](#σημειώσεις-πρώτης-εκτέλεσης-μη-υπογεγραμμένες-εκδόσεις): επιλέξτε **Περισσότερες πληροφορίες** -> **Εκτέλεση οπωσδήποτε**· επαναφέρετε τα ψευδώς θετικά του Defender από το **Ιστορικό προστασίας**. |
| Μια νέα εγκατάσταση δεν έχει ορισμούς | Εκτελέστε την εντολή Εργαλεία -> Οδηγός ρύθμισης ή την εντολή Εργαλεία -> Λήψη προτεινόμενων πόρων. Για μη αυτόματη εισαγωγή, κρατήστε το ZIP του Yomitan ακέραιο (μην το αποσυμπιέσετε). |
| Η Προσθήκη λεξικού κολλάει ή αποτυγχάνει | Σημειώστε το τελευταίο ορατό στάδιο και επισυνάψτε τα αρχεία καταγραφής (δείτε «Πού είναι τα αρχεία καταγραφής;» παρακάτω). Αναφέρετε στην αναφορά το όνομα, την πηγή και το μέγεθος του ZIP του λεξικού. |
| Πού είναι τα αρχεία καταγραφής; | Χρησιμοποιήστε την εντολή Βοήθεια -> Άνοιγμα φακέλου καταγραφής ή ανοίξτε το `%USERPROFILE%\.anki_miner\anki_miner.log` στα Windows ή το `~/.anki_miner/anki_miner.log` σε macOS/Linux. Τα παλαιότερα αρχεία καταγραφής έχουν τις καταλήξεις `.1` έως `.5`. Στείλτε και το `anki_miner.crash` αν υπάρχει (μια κατάρρευση που έκλεισε την εφαρμογή γράφει τη στοίβα της σε αυτό το αρχείο, όχι στην καταγραφή), καθώς και το `anki_miner.child.log`, που περιέχει την έξοδο μιας βοηθητικής διεργασίας. |
| Αναφορά σφάλματος        | Η Βοήθεια -> Εξαγωγή διαγνωστικών… γράφει ένα ZIP σε θέση της επιλογής σας, με τα αρχεία καταγραφής (`anki_miner.log` και τις εναλλαγές του, `anki_miner.crash`, `anki_miner.child.log`), το `settings.json` σας, τα αρχεία ρυθμίσεων και κατάστασης περιβάλλοντος, στιγμιότυπα ουρών και δηλωτικά λήψεων, καθώς και αναφορές για την κατάσταση του υπολογιστή και της εφαρμογής (`environment.txt`, `health.txt`, `resources.txt`, `stores.txt`, `disk.txt`, `screens.txt`). Ελέγξτε το πριν το ανεβάσετε, γιατί περιέχει διαδρομές και ονόματα αρχείων από τον υπολογιστή σας. Τίποτα δεν ανεβαίνει αυτόματα. |
| Περισσότερη διαγνωστική καταγραφή | Ορίστε `ANKI_MINER_LOG_LEVEL=DEBUG` πριν ξεκινήσετε το Anki Miner για να καταγράφονται λεπτομέρειες των yt-dlp, urllib3 και fugashi. Η προεπιλογή είναι `WARNING`· οι καταγραφές του Anki Miner παραμένουν σε DEBUG. |
| Ο ήχος είναι σε λάθος γλώσσα | Το εργαλείο επιλέγει το κανάλι ήχου της γλώσσας εξόρυξης, αλλιώς το πρώτο. Επιλέξτε το εσείς με το Κανάλι ήχου… (Βίντεο -> Μεμονωμένο). |
| Υπότιτλοι εκτός συγχρονισμού | Χρησιμοποιήστε τη ρύθμιση μετατόπισης υποτίτλων στο περιβάλλον της εφαρμογής (εύρος ±300 δευτερόλεπτα). |

## Συνεισφορά

Κάθε είδους συνεισφορά είναι ευπρόσδεκτη.
Αν θέλετε να στηρίξετε το έργο, μοιραστείτε το με άλλους που μπορεί να ωφεληθούν.

- Είστε νέοι εδώ; Ξεκινήστε από το [CONTRIBUTING.md](../CONTRIBUTING.md).
- Επισκόπηση αρχιτεκτονικής: [ARCHITECTURE.md](../ARCHITECTURE.md).
- Κώδικας δεοντολογίας: [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md).
- Ασφάλεια: [SECURITY.md](../SECURITY.md).

Αναφορές σφαλμάτων και αιτήματα λειτουργιών -> [Issues](https://github.com/0xzerolight/anki_miner/issues).
Γενικές ερωτήσεις και συζήτηση -> [Discussions](https://github.com/0xzerolight/anki_miner/discussions) ή [Discord](https://discord.com/invite/aDtQyZzUVP).

## Ιδιαίτερες ευχαριστίες

Ειλικρινείς ευχαριστίες σε όσους έκαναν εξαιρετική συνεισφορά στο έργο:

- ★ **[StyraxBenzoin](https://github.com/StyraxBenzoin)** - Εξαιρετικές προτάσεις λειτουργιών, δοκιμές νέων εκδόσεων, ανάπτυξη της κοινότητας.
- ★ **[rob-olvr](https://github.com/rob-olvr)** - Εξαιρετικές προτάσεις λειτουργιών, ανάπτυξη της κοινότητας και διαχείριση στο Discord.

Δείτε το [CONTRIBUTORS.md](../CONTRIBUTORS.md) για όλους όσοι έχουν συνεισφέρει με οποιονδήποτε τρόπο στο έργο.


## Άδεια χρήσης

GNU General Public License v3.0. Δείτε το [LICENSE](../LICENSE).
