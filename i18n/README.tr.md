<!-- i18n-source: README.md sha256:19e0751568de3bbb -->

<h1 align="center">
  <img src="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/anki_miner/gui/resources/icons/anki_miner.svg" height="76" align="absmiddle" alt=""> Anki Miner
</h1>

<p align="center">
<a href="https://pypi.org/project/anki-miner/"><img src="https://img.shields.io/pypi/v/anki-miner.svg" alt="PyPI sürümü"></a>
<a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"></a>
<a href="https://www.gnu.org/licenses/gpl-3.0"><img src="https://img.shields.io/badge/License-GPLv3-blue.svg" alt="Lisans: GPL v3"></a>
<a href="https://github.com/0xzerolight/anki_miner/releases/latest"><img src="https://img.shields.io/github/downloads/0xzerolight/anki_miner/total.svg" alt="GitHub indirmeleri"></a>
<a href="https://github.com/0xzerolight/anki_miner/stargazers"><img src="https://img.shields.io/github/stars/0xzerolight/anki_miner?style=social" alt="GitHub yıldızları"></a>
<a href="https://discord.com/invite/aDtQyZzUVP"><img src="https://img.shields.io/discord/1517634859110240326?logo=discord&logoColor=white&label=Discord&color=5865F2" alt="Discord topluluğu"></a>
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
<a href="README.el.md">Ελληνικά</a> ·
<a href="README.ro.md">Română</a> ·
<b>Türkçe</b>
</p>
<!-- i18n-nav:end -->

<p align="center">
Japonca, Çince, Korece ve diğer yirmi dokuz dildeki özgün içerikleri Anki kelime kartlarına dönüştürün.
</p>

<p align="center">
Android'de de var - <a href="https://github.com/0xzerolight/anki_miner_android">Android için Anki Miner</a>.
</p>

<p align="center">
Anki Miner işinize yaradıysa lütfen bir ⭐ yıldız bırakın - başkalarının onu bulmasına yardımcı olur :).
</p>


# <p align="center">Toplama Demosu</p>

![Anki Miner Tanıtımı](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.gif)

<p align="center">⬇️ <a href="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.mp4">Sesli tam demo (MP4)</a></p>

### Örnek kartlar

| ![ホント](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.gif) | ![いちゃいちゃ](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.gif) | ![代](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.gif) |
|:--:|:--:|:--:|
| ⬇️ [MP4 (sesli)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.mp4) | ⬇️ [MP4 (sesli)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.mp4) | ⬇️ [MP4 (sesli)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.mp4) |

## Kurulum

### Gereksinimler

- [AnkiConnect](https://ankiweb.net/shared/info/2055492159) eklentisi (kod `2055492159`) yüklü **Anki**
- **ffmpeg** + **libmpv** (yalnızca video önizlemesi için) - yalnızca pip/pipx ile veya kaynaktan kurarken gerekir.

Platformunuza uygun dosyayı [en son sürümden](https://github.com/0xzerolight/anki_miner/releases/latest) indirin:

| Platform | İndirme |
|----------|----------|
| Windows | `AnkiMiner-*-Setup.exe` |
| macOS (Apple Silicon) | `AnkiMiner-*-macOS-arm64.dmg` |
| macOS (Intel) | `AnkiMiner-*-macOS-x86_64.dmg` ¹ |
| Linux (Debian/Ubuntu) | `anki-miner_*_amd64.deb` |
| Linux (diğer) | `AnkiMiner-*-Linux-x86_64.AppImage` |

¹ AVIF ekran görüntüleri ve oluşturulan altyazılar için sessizlik giderme bu sürümde yoktur.

### İlk çalıştırma notları (imzasız sürümler)

- **macOS**: `.dmg` dosyasını açın, **AnkiMiner**'ı **Uygulamalar** klasörüne sürükleyin, ardından başlatın. Uygulama noter onaylı olmadığı için ilk açılış engellenir: iletişim kutusunu kapatın, uygulamayı yeniden açın ve **Yine de Aç**'ı seçin (**Sistem Ayarları** -> **Gizlilik ve Güvenlik** altında da bulunur). Yalnızca bir kez gerekir.
- **Linux AppImage**: çalıştırmadan önce dosyayı çalıştırılabilir yapın - `chmod +x AnkiMiner-*.AppImage` veya dosya yöneticinizde **Özellikler** -> **Çalıştırmaya izin ver**.
- **Windows SmartScreen**: **Daha fazla bilgi** -> **Yine de çalıştır**.
- **Windows Defender hatalı algılaması**: **Koruma geçmişi**'nden geri yükleyin veya [Microsoft'a bildirin](https://www.microsoft.com/en-us/wdsi/filesubmission).

<details>
<summary><strong>PyPI'den kurulum (Python 3.11+)</strong></summary>

```bash
pipx install anki-miner   # or: pip install anki-miner
anki_miner_gui
```

Japonca, Endonezce ve İbranice için ek bir şey gerekmez. Başka bir toplama dili için o dilin motorunu ekleyin:

```bash
pipx install "anki-miner[languages]"   # all; or one of [zh], [ko], [yue], [en], [ca], [de], [pt], [fr], [es], [it], [nl], [nb], [ro], [el], [fi], [hu], [hr], [sv], [pl], [lt], [da], [ru], [uk], [sl], [tr], [th], [vi]
```

Arapça ve Farsça için ek paket yoktur. Bu dillerin verileri ve Avrupa dillerinin spaCy modelleri uygulama içinde Ayarlar -> Toplama Dili bölümünden indirilir; yukarıdaki indirilebilir sürümler de her şeyi oradan alır.

</details>

<details>
<summary><strong>Kaynaktan kurulum</strong></summary>

```bash
git clone https://github.com/0xzerolight/anki_miner.git
cd anki_miner
pip install -e .
anki_miner_gui
```

Geliştirme ortamının tam kurulumu için [CONTRIBUTING.md](../CONTRIBUTING.md) dosyasına bakın.

</details>

## Sekmeler

- **Video** - tek bir video/altyazı çiftini, toplu bir klasörü, YouTube veya Bilibili URL'lerini ya da bütün bir diziyi sıklığa göre sıralanmış tek bir desteye toplayın.
- **Sesli Kitaplar** - sesli kitapları, podcast'leri, radyoyu, şarkıları (ses + altyazı/transkript çiftleri) toplayın.
- **Okuma** - mangayı (mokuro), romanları (`.epub`, `.txt`; tek kitap veya bütün bir klasör), bağımsız altyazı dosyalarını veya yapıştırılan metni toplayın.
- **Analiz** - toplama geçmişi, zorluk sıralamaları, kilometre taşları.
- **Yardımcılar** - altyazı oluşturun (yerel Whisper), altyazıların zamanlamasını düzeltin (ffsubsync/alass), medyayı yalnızca diyalog içeren sese yoğunlaştırın, yt-dlp'nin desteklediği her siteden video/ses/altyazı indirin, hazır bir destenin öğrenmeye değer kısmını yeni bir desteye kopyalayın, var olan kartlardaki alanları tamamlayın, manga sayfa görüntülerini OCR ile .mokuro dosyalarına dönüştürün (mokuro) ve bir sesli kitabı kitabın metniyle eşleyin (Kitap Eşitleme).
- **Ayarlar** - ayarlanabilen her şey.

## Diğer Özellikler

- Toplama dilleri - Japonca, Çince, Korece, İngilizce, Katalanca, Almanca, Portekizce, Fransızca, İspanyolca, İtalyanca, Felemenkçe, Norveççe (Bokmål), Rumence, Yunanca, Fince, Macarca, Hırvatça, İsveççe, Lehçe, Litvanca, Danca, Türkçe, Endonezce, Rusça, Arapça, Tayca, Farsça, Slovence, Ukraynaca, Vietnamca, Kantonca ve İbranice; Ayarlar'dan değiştirilir. Japonca dışındaki diller motorlarını uygulama içinde indirir; Endonezce ve İbranice için motor gerekmez.
- Kelime Düzenleyici - kartlar oluşturulmadan önce her aday kelimeyi; sahnesi, manga sayfası ve sözlük kaydıyla yan yana gözden geçirin.
- Bir çalıştırmayı geri alın - bir çalıştırmanın az önce oluşturduğu notları doğrudan sonuç penceresinden silin.
- Kapsamlı filtreleme: i+1, sıklık derecesi aralığı, kara liste, regex, kelime kümeleri ve daha fazlası.
- Çevrimdışı Yomitan sözlüğü içe aktarma - tanımlar, perde vurgusu, sıklık - önceliğe göre zincirlenir.
- Önceliğe göre zincirlenen birden çok sıklık listesi.
- Yerel ses paketlerinden, JapanesePod101'den, Google TTS'ten veya Microsoft Edge TTS'ten kartlara kelime sesi.
- Okuma kartlarında Google Translate TTS'ten veya Japonca ve Korece için Naver Papago'dan cümle sesi (varsayılan olarak kapalı).
- Yomitan tarzında, sözlüğe özel sözlükçe biçimlendirmesi.
- Gömülü libmpv video önizlemesi - kelimeleri seçerken bir kelimenin sahnesini oynatın veya altyazı zamanlamasını canlı oynatmayla ince ayarlayın.
- Hareketli ekran görüntüleri (yukarıdaki örnek kartlara bakın).
- Ayar profilleri - adlandırılmış yapılandırmaları kaydedin ve başlık çubuğundan aralarında geçiş yapın.
- Toplanan kartları yeniden biçimlendir - geçerli kart biçiminizi daha önce oluşturduğunuz kartlara yeniden uygulayın (Yardımcılar → Kart Tamamla).

<details>
<summary><strong>Yerleşik temalar (29)</strong></summary>

- **Ayu** - Light, Mirage, Dark
- **Catppuccin** - Latte (açık); Frappé, Macchiato, Mocha (koyu)
- **Dracula** - Dracula, Alucard
- **Everforest** - Light, Dark
- **GitHub** - Light; Dark, Dark Dimmed
- **Gruvbox** - Light Medium, Dark Medium
- **Kanagawa** - Lotus (açık), Wave (koyu)
- **Rosé Pine** - Dawn (açık); Main, Moon (koyu)
- **Solarized** - Light, Dark
- **Bağımsız** - Light, Dark, Sakura, Nord, One Dark, Tokyo Night

Tema lisansları: [LICENSE-THEMES.md](../LICENSE-THEMES.md). 
Başka bir tema eklenmesini mi istiyorsunuz? Bir GitHub Issue ile önerin.

</details>

<details>
<summary><strong>Nasıl Çalışır</strong></summary>

1. **Altyazıları okur** ve metni tek tek kelimelere ayırır.
2. **Filtreleyerek** henüz bilmediğiniz içerik kelimelerini bırakır - isterseniz listeyi Kelime Düzenleyici'de kendiniz gözden geçirirsiniz.
3. Her satır için videodan **bir ekran görüntüsü ve ses klibi alır**.
4. Yapılandırdığınız çevrimdışı sözlüklerde **tanımları arar**.
5. **Hazır kartları Anki'ye gönderir.**

</details>

## Önerilen Kaynaklar

Her toplama dili için sözlükler ve sıklık listeleri, indirme bağlantılarıyla birlikte: [RESOURCES.md](../RESOURCES.md). Kurulum sihirbazı, toplama dilinize uygun seti önerir.

<details>
<summary><strong>JMnedict Lisansı</strong></summary>

[JMnedict](https://www.edrdg.org/enamdict/enamdict_doc.html) kaynağından türetilmiş, uygulamayla gelen ad kelime kümelerini kullanır (JMdict/EDICT projesi, EDRDG, CC BY-SA 4.0).

</details>

## Sorun Giderme

| Sorun                    | Çözüm                                                                         |
|--------------------------|----------------------------------------------------------------------------------|
| "Anki'ye bağlanılamıyor" | Anki'yi başlatın ve AnkiConnect'in yüklü olduğundan emin olun.                                  |
| "Deste bulunamadı"         | Ayarlar -> Kartlar ve Anki bölümünden var olan bir deste seçin. Desteler sizin için oluşturulmaz; yeni bir desteye ihtiyacınız varsa önce Anki'de oluşturun. |
| "Not türü bulunamadı"    | Not türünüzün alan adlarını Ayarlar -> Kartlar ve Anki bölümünde yapılandırın.               |
| "ffmpeg bulunamadı"       | ffmpeg'i yükleyip PATH'e ekleyin.                                               |
| Tanım bulunamıyor     | Ayarlar -> Sözlükler -> Sözlük ekle… ile bir Yomitan sözlüğü ekleyin. |
| Windows yükleyicisi açılmıyor / SmartScreen uyarısı | [İlk çalıştırma notları](#i̇lk-çalıştırma-notları-imzasız-sürümler) bölümüne bakın: **Daha fazla bilgi** -> **Yine de çalıştır** öğesini seçin; Defender'ın hatalı algılamalarını **Koruma geçmişi**'nden geri yükleyin. |
| Yeni kurulumda tanım yok | Araçlar -> Kurulum sihirbazı veya Araçlar -> Önerilen kaynakları indir öğesini çalıştırın. Elle içe aktarırken Yomitan ZIP dosyasını olduğu gibi bırakın (açmayın). |
| Sözlük ekleme takılıyor veya başarısız oluyor | Görünen son aşamayı not edin ve günlükleri ekleyin (aşağıdaki "Günlükler nerede?" satırına bakın). Rapora sözlük ZIP dosyasının adını, kaynağını ve boyutunu ekleyin. |
| Günlükler nerede?      | Yardım -> Günlük klasörünü aç öğesini kullanın veya Windows'ta `%USERPROFILE%\.anki_miner\anki_miner.log`, macOS/Linux'ta `~/.anki_miner/anki_miner.log` dosyasını açın. Döndürülen günlükler `.1` ile `.5` arasındaki uzantıları kullanır. Varsa `anki_miner.crash` dosyasını da gönderin (uygulamayı çökerten bir hata, yığın izini günlüğe değil bu dosyaya yazar) ve bir yardımcı işlemin çıktısını içeren `anki_miner.child.log` dosyasını da ekleyin. |
| Hata bildirme          | Yardım -> Tanılama verilerini dışa aktar…, seçtiğiniz bir konuma bir ZIP yazar. Bu ZIP günlükleri (`anki_miner.log` ve döndürülmüş kopyaları, `anki_miner.crash`, `anki_miner.child.log`), `settings.json` dosyanızı, yapılandırma ve arayüz durumu dosyalarınızı, sıra anlık görüntülerini ve indirme bildirimlerini, ayrıca makine ve uygulama durumu için oluşturulan raporları (`environment.txt`, `health.txt`, `resources.txt`, `stores.txt`, `disk.txt`, `screens.txt`) içerir. Bilgisayarınızdaki dosya yollarını ve dosya adlarını içerdiği için yüklemeden önce gözden geçirin. Hiçbir şey otomatik olarak yüklenmez. |
| Daha ayrıntılı tanılama günlüğü | Üçüncü taraf yt-dlp, urllib3 ve fugashi ayrıntılarını yakalamak için Anki Miner'ı başlatmadan önce `ANKI_MINER_LOG_LEVEL=DEBUG` ayarlayın. Varsayılan `WARNING` düzeyidir; Anki Miner günlükleri DEBUG düzeyinde kalır. |
| Ses yanlış dilde  | Araç, toplama dilinin ses parçasını, yoksa ilk parçayı seçer. Ses parçası… ile kendiniz seçin (Video -> Tekli). |
| Altyazılar senkronize değil    | Arayüzdeki altyazı kayması denetimini kullanın (±300 saniye aralığında).                 |

## Katkıda Bulunma

Her türlü katkıya açığız.
Projeyi desteklemek istiyorsanız, yararlanabilecek başkalarıyla paylaşın.

- Yeni misiniz? [CONTRIBUTING.md](../CONTRIBUTING.md) ile başlayın.
- Mimariye genel bakış: [ARCHITECTURE.md](../ARCHITECTURE.md).
- Davranış Kuralları: [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md).
- Güvenlik: [SECURITY.md](../SECURITY.md).

Hata bildirimleri ve özellik istekleri -> [Issues](https://github.com/0xzerolight/anki_miner/issues).
Genel sorular ve tartışma -> [Discussions](https://github.com/0xzerolight/anki_miner/discussions) veya [Discord](https://discord.com/invite/aDtQyZzUVP).

## Özel Teşekkürler

Projeye olağanüstü katkılarda bulunan kişilere içten teşekkürler:

- ★ **[StyraxBenzoin](https://github.com/StyraxBenzoin)** - Harika özellik önerileri, yeni sürüm testleri, topluluk oluşturma.
- ★ **[rob-olvr](https://github.com/rob-olvr)** - Mükemmel özellik önerileri, Discord'da topluluk oluşturma ve moderasyon.

Projeye herhangi bir şekilde katkıda bulunan herkes için [CONTRIBUTORS.md](../CONTRIBUTORS.md) dosyasına bakın.


## Lisans

GNU General Public License v3.0. Bkz. [LICENSE](../LICENSE).
