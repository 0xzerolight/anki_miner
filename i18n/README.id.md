<!-- i18n-source: README.md sha256:5648ea423d8eee0f -->

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
<b>Bahasa Indonesia</b> ·
<a href="README.vi.md">Tiếng Việt</a> ·
<a href="README.zh_cn.md">简体中文</a> ·
<a href="README.zh_tw.md">繁體中文</a> ·
<a href="README.it.md">Italiano</a>
</p>
<!-- i18n-nav:end -->

<p align="center">
Ubah konten Jepang asli menjadi kartu kosakata Anki.
</p>

<p align="center">
Juga di Android - <a href="https://github.com/0xzerolight/anki_miner_android">Anki Miner untuk Android</a>.
</p>

<p align="center">
Beri ⭐ bintang jika Anki Miner membantu Anda - ini membantu orang lain menemukannya :).
</p>


# <p align="center">Demo Mining</p>

![Anki Miner Showcase](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.gif)

<p align="center">⬇️ <a href="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.mp4">Demo lengkap dengan suara (MP4)</a></p>

### Contoh kartu

| ![ホント](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.gif) | ![いちゃいちゃ](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.gif) | ![代](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.gif) |
|:--:|:--:|:--:|
| ⬇️ [MP4 (sound)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.mp4) | ⬇️ [MP4 (sound)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.mp4) | ⬇️ [MP4 (sound)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.mp4) |

## Instalasi

### Persyaratan

- **Anki** dengan add-on [AnkiConnect](https://ankiweb.net/shared/info/2055492159) (kode `2055492159`)
- **ffmpeg** + **libmpv** (hanya untuk pratinjau video) - hanya diperlukan saat memasang lewat pip/pipx atau dari sumber.

Ambil unduhan untuk platform Anda dari [rilis terbaru](https://github.com/0xzerolight/anki_miner/releases/latest):

| Platform | Unduhan |
|----------|----------|
| Windows | `AnkiMiner-*-Setup.exe` |
| macOS (Apple Silicon / M1-M4) | `AnkiMiner-macOS-arm64.tar.gz` |
| macOS (Intel) | `AnkiMiner-macOS-x86_64.tar.gz` ¹ |
| Linux (Debian/Ubuntu) | `anki-miner_*_amd64.deb` |
| Linux (lainnya) | `AnkiMiner-*-Linux-x86_64.AppImage` |

¹ Tidak termasuk pembuatan subtitel Whisper lokal dan tangkapan layar AVIF. Untuk fungsionalitas penuh: `pipx install "anki-miner[asr]"`.

### Catatan penjalanan pertama (build tanpa tanda tangan)

- **macOS**: Gatekeeper memblokir aplikasi. Ekstrak dulu, lalu `xattr -dr com.apple.quarantine AnkiMiner/`
- **Windows SmartScreen**: **Info lainnya** -> **Tetap jalankan**.
- **Positif palsu Windows Defender**: pulihkan dari **Riwayat perlindungan** atau [laporkan ke Microsoft](https://www.microsoft.com/en-us/wdsi/filesubmission).

<details>
<summary><strong>Instal dari PyPI (Python 3.11+)</strong></summary>

```bash
pipx install anki-miner   # or: pip install anki-miner
anki_miner_gui
```

</details>

<details>
<summary><strong>Instal dari sumber</strong></summary>

```bash
git clone https://github.com/0xzerolight/anki_miner.git
cd anki_miner
pip install -e .
anki_miner_gui
```

Untuk pengaturan pengembangan lengkap, lihat [CONTRIBUTING.md](../CONTRIBUTING.md).

</details>

## Tab

- **Video** - mining satu pasang video/subtitel, folder batch, atau URL YouTube.
- **Pembuat Dek** - mining seluruh serial menjadi satu dek yang diurutkan berdasarkan frekuensi.
- **Buku Audio** - mining buku audio, podcast, radio, lagu (pasangan audio + subtitel/transkrip).
- **Bacaan** - mining manga (mokuro), novel (`.epub`, `.txt`; satu buku atau seluruh folder), berkas subtitel mandiri, atau teks Jepang yang ditempel.
- **Analitik** - riwayat mining, peringkat kesulitan, milestone.
- **Utilitas** - membuat subtitel (Whisper lokal), mengatur ulang waktu subtitel (ffsubsync/alass), memadatkan media menjadi audio berisi dialog saja, menyalin bagian yang layak dipelajari dari dek siap pakai ke dek baru, dan mengisi ulang bidang pada kartu yang sudah ada.
- **Pengaturan** - semua yang bisa dikonfigurasi.

## Fitur Lainnya

- Word Curator - tinjau setiap kata kandidat sebelum kartu dibuat, lengkap dengan adegan, halaman manga, dan entri kamus berdampingan.
- Batalkan sebuah proses - hapus catatan yang baru saja dibuat oleh sebuah proses, langsung dari dialog hasilnya.
- Filter yang luas: i+1, rentang peringkat frekuensi, daftar hitam, regex, kumpulan kata, dan lainnya.
- Impor kamus Yomitan offline - definisi, aksen nada, frekuensi - dirangkai berdasarkan prioritas.
- Beberapa daftar frekuensi dirangkai berdasarkan prioritas.
- Audio kata pada kartu dari paket audio lokal, JapanesePod101, atau Google TTS.
- Audio kalimat pada kartu Bacaan dari Google Translate TTS atau Naver Papago (nonaktif secara default).
- Gaya glosarium per kamus, bergaya Yomitan.
- Pratinjau video libmpv tersemat - putar adegan sebuah kata saat mengkurasi, atau sesuaikan waktu subtitel dengan pemutaran langsung.
- Tangkapan layar beranimasi (lihat contoh kartu di atas).
- Profil pengaturan - simpan konfigurasi bernama dan beralih di antaranya dari header.
- Tata Ulang Kartu Hasil Mining - terapkan ulang gaya kartu Anda saat ini ke kartu yang sudah Anda buat (menu Alat).

<details>
<summary><strong>Tema bawaan (29)</strong></summary>

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

Lisensi tema: [LICENSE-THEMES.md](../LICENSE-THEMES.md). 
Ingin tema lain ditambahkan? Ajukan lewat GitHub Issue.

</details>

<details>
<summary><strong>Cara Kerjanya</strong></summary>

1. **Baca subtitel** dan pecah bahasa Jepang menjadi kata per kata.
2. **Filter** ke kata isi yang belum Anda kenal - opsional meninjau sendiri daftarnya di Word Curator.
3. **Ambil tangkapan layar dan klip audio** dari video untuk setiap baris.
4. **Cari definisi** di kamus offline yang Anda konfigurasi, opsional beralih ke Jisho online jika diaktifkan (lebih lambat, dibatasi laju).
5. **Kirim kartu yang sudah jadi ke Anki.**

</details>

## Sumber Daya yang Direkomendasikan

| Jenis | Sumber Daya | Unduhan | Tambah melalui |
|------|----------|----------|---------|
| Kamus | [JMdict](https://github.com/yomidevs/jmdict-yomitan) | [Yomitan zip](https://github.com/yomidevs/jmdict-yomitan/releases/latest/download/JMdict_english.zip) | Tambahkan kamus… |
| Kamus | [Jitendex](https://jitendex.org/) | [Yomitan zip](https://github.com/stephenmk/stephenmk.github.io/releases/latest/download/jitendex-yomitan.zip) | Tambahkan kamus… |
| Kamus | [Bee's Character Dictionary](https://characterdictionary.tokyo/) | Dibuat di situs | Tambahkan kamus… |
| Aksen Nada | [Kanjium](https://github.com/mifunetoshiro/kanjium) | [TSV](https://raw.githubusercontent.com/mifunetoshiro/kanjium/master/data/source_files/raw/accents.txt) | Aksen Nada -> Tambahkan sumber aksen nada… |
| Aksen Nada | [アクセント辞典v2](https://learnjapanese.moe/yomichan/#dictionaries) | [Drive](https://drive.google.com/drive/folders/1tTdLppnqMfVC5otPlX_cs4ixlIgjv_lH) | Aksen Nada -> Tambahkan sumber aksen nada… |
| Frekuensi | [JPDB v2.2 Kana](https://github.com/Kuuuube/yomitan-dictionaries) | [Yomitan zip](https://github.com/Kuuuube/yomitan-dictionaries/raw/main/dictionaries/JPDB_v2.2_Frequency_Kana_2024-10-13.zip) | Frekuensi -> Tambahkan sumber frekuensi… |
| Frekuensi | [BCCWJ SUW+LUW](https://github.com/Kuuuube/yomitan-dictionaries) | [Yomitan zip](https://github.com/Kuuuube/yomitan-dictionaries/raw/main/dictionaries/BCCWJ_SUW_LUW_combined.zip) | Frekuensi -> Tambahkan sumber frekuensi… |
| Audio kata | [local-audio-yomichan](https://github.com/yomidevs/local-audio-yomichan) | Torrent koleksi atau `android.db` yang dihasilkan | Audio -> Tambahkan sumber audio… |


<details>
<summary><strong>Lisensi JMnedict</strong></summary>

Menggunakan kumpulan kata nama bawaan yang berasal dari [JMnedict](https://www.edrdg.org/enamdict/enamdict_doc.html) (proyek JMdict/EDICT, EDRDG, CC BY-SA 4.0).

</details>

## Pemecahan Masalah

| Masalah                    | Solusi                                                                         |
|--------------------------|----------------------------------------------------------------------------------|
| "Tidak dapat terhubung ke Anki" | Jalankan Anki dan pastikan AnkiConnect sudah terpasang.                                  |
| "Dek tidak ditemukan"         | Pilih dek yang sudah ada di Pengaturan -> Kartu & Anki. Dek tidak dibuat otomatis; buat dulu di Anki jika Anda perlu dek baru. |
| "Tipe catatan tidak ditemukan"    | Konfigurasikan nama bidang tipe catatan Anda di Pengaturan -> Kartu & Anki.               |
| "ffmpeg tidak ditemukan"       | Pasang ffmpeg dan tambahkan ke PATH.                                               |
| Tidak ada definisi ditemukan     | Tambahkan kamus Yomitan di Pengaturan -> Tambahkan kamus… (disarankan), atau aktifkan fallback Jisho (lebih lambat, dibatasi laju). |
| Installer Windows tidak mau terbuka / peringatan SmartScreen | Lihat [Catatan penjalanan pertama](#catatan-penjalanan-pertama-build-tanpa-tanda-tangan): pilih **Info lainnya** -> **Tetap jalankan**; pulihkan positif palsu Defender dari **Riwayat perlindungan**. |
| Instalasi baru tidak punya definisi | Jalankan Alat -> Wizard Penyiapan atau Alat -> Unduh Sumber Daya yang Direkomendasikan. Untuk impor manual, biarkan ZIP Yomitan utuh (jangan diekstrak). |
| Tambahkan kamus macet atau gagal | Catat tahap terakhir yang terlihat dan lampirkan log (lihat "Di mana letak lognya?" di bawah). Sertakan nama ZIP kamus, sumber, dan ukurannya dalam laporan. |
| Di mana letak lognya?      | Gunakan Bantuan -> Buka Folder Log, atau buka `%USERPROFILE%\.anki_miner\anki_miner.log` di Windows atau `~/.anki_miner/anki_miner.log` di macOS/Linux. Log yang dirotasi memakai akhiran `.1` sampai `.5`. |
| Melaporkan bug          | Bantuan -> Ekspor Diagnostik… menulis ZIP berisi log dan detail sistem ke lokasi pilihan Anda. Tinjau dulu sebelum mengunggahnya karena berisi jalur berkas dan nama berkas dari komputer Anda. Tidak ada yang diunggah otomatis. |
| Logging diagnostik lebih rinci | Atur `ANKI_MINER_LOG_LEVEL=DEBUG` sebelum menjalankan Anki Miner untuk menangkap detail yt-dlp, urllib3, dan fugashi pihak ketiga. Default-nya `WARNING`; log Anki Miner tetap di DEBUG. |
| Audio bahasanya salah  | Alat ini mencoba trek audio Jepang terlebih dahulu, lalu beralih ke default.      |
| Subtitel tidak sinkron    | Gunakan kontrol offset subtitel di GUI (rentang ±300 detik).                 |

## Peta Jalan

Daftar ide untuk versi mendatang Anki Miner. Bukan dalam urutan prioritas. Permintaan fitur diutamakan.
- Usulkan fitur - [Buka issue](https://github.com/0xzerolight/anki_miner/issues).
- Diskusikan peta jalan - [Discussions](https://github.com/0xzerolight/anki_miner/discussions).

- **Fitur**:
  - [x] Pemilihan bahasa UI.
  - [x] Tab pembuatan subtitel lokal: Tab opsional untuk membuat subtitel secara lokal.
  - [x] Tab Bacaan: Mining manga dan buku.
  - [x] Alat pengisian ulang (backfill).
  - [ ] Pustaka media: Perluas tab Analitik untuk menampilkan pustaka media lokal di semua bentuk media.
  - [ ] Pengunduhan subtitel otomatis.

- **Jangka panjang**:
  - [x] Port Android -- https://github.com/0xzerolight/anki_miner_android
  - [ ] Di luar bahasa Jepang: Mining bahasa lain.
  - [ ] Ekstensi peramban Anki Miner.


## Berkontribusi

Kontribusi dalam bentuk apa pun sangat diterima.
Jika Anda ingin mendukung proyek ini, silakan bagikan ke orang lain yang mungkin memperoleh manfaat darinya.

- Baru di sini? Mulai dengan [CONTRIBUTING.md](../CONTRIBUTING.md).
- Gambaran arsitektur: [ARCHITECTURE.md](../ARCHITECTURE.md).
- Kode Etik: [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md).
- Keamanan: [SECURITY.md](../SECURITY.md).

Laporan bug dan permintaan fitur -> [Issues](https://github.com/0xzerolight/anki_miner/issues).
Pertanyaan umum dan diskusi -> [Discussions](https://github.com/0xzerolight/anki_miner/discussions) atau [Discord](https://discord.com/invite/aDtQyZzUVP).

## Ucapan Terima Kasih Khusus

Terima kasih yang tulus kepada orang-orang yang memberikan kontribusi luar biasa untuk proyek ini:

- ★ **[StyraxBenzoin](https://github.com/StyraxBenzoin)** - Saran fitur yang brilian, pengujian rilis baru, pembangunan komunitas.
- ★ **[rob-olvr](https://github.com/rob-olvr)** - Saran fitur yang unggul, pembangunan komunitas dan moderasi di Discord.

Lihat [CONTRIBUTORS.md](../CONTRIBUTORS.md) untuk semua orang yang telah memberikan kontribusi dalam bentuk apa pun untuk proyek ini.


## Lisensi

GNU General Public License v3.0. Lihat [LICENSE](../LICENSE).
