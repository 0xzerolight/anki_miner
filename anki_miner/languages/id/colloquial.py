"""Indonesian colloquial -> formal table (C.5 N0): the first ladder rung, the stopword expansion, the Formal field.

Generated, never hand-edited. Source: IndoCollex (Wibowo et al., ACL-IJCNLP 2021), ``haryoa/indo-collex`` commit
df626e812cd5924794fabf40003368a69f4e953c, ``dict/inforformal-formal-Indonesian-dictionary.tsv`` (MIT, Copyright (c)
2021 Haryo Akbarianto Wibowo; notice in ``licenses/indocollex/LICENSE``), 2,622 pairs. Kept: lowercase letter
spellings (hyphens allowed; a formal side may be a phrase) whose every formal word is a wty-id-en (revision
2026.09.19) headword, which drops the digit rows (``10rb``) and the unattested targets. The 28-row C.5 curated
core is laid over it (``lo``/``lu`` -> ``kamu``, ``bgt``/``banget`` -> ``sangat``, ``bikin`` -> ``membuat``);
``gue``/``gua``/``gw`` keep IndoCollex's formal ``saya`` (plan D7). 1988 pairs, one per line, ``informal formal``.

``ID_COLLOQUIAL_CORE`` is the curated core alone. Only it expands the stopword tier
(:func:`anki_miner.languages.id.morphology.is_stopword`): IndoCollex is crowd-derived and its formal side is
not a reviewed function-word judgement, so reading the whole table through would make a second, unreviewed
stopword list (plan D6).
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

_PAIRS = """
aa kakak
aah ah
aamiin amin
aamin amin
abg remaja tanggung
abi ayah
abng abang
acheh aceh
activities aktifitas
ad ada
adaa ada
adek adik
adha ada
adk arsip data komputer
adlh adalah
adoh aduh
aduhh aduh
aduhhh aduh
adus mandi
adventures petualangan
ae saja
agan juragan
ahhh ah
ahhhh ah
aing aku
airline perusahaan penerbangan
aj saja
aja saja
ajaa saja
ajaaa saja
ajaaaa aja
ajah saja
ajakin mengajak
aje saja
ajg anjing
ajh aja
ajig anjing
ak aku
akeh banyak
akn akan
akoh aku
aktifitas aktivitas
akuh aku
akutu aku itu
akutuh aku itu
akuu aku
akuuu aku
akuuuu aku
alaa ala
alai anak layangan
alamin mengalami
alay anak layangan
algo algoritme
alhamdulillaah alhamdulillah
allhamdulillah alhamdulillah
alloh allah
alon pelan
alus halus
ambulan ambulans
america amerika
amigos teman-teman
amiiin amin
amiin amin
aminnn amin
amo cinta
ancur hancur
ane aku
angels malaikat
anget hangat
anggep anggap
animals binatang
anjay anjing
anjeng anjing
anjg anjing
anjirr anjing
anjirrr anjing
ank anak
anter antar
antum anda
anying anjing
anymore lagi
ap apa
apaa apa
apaaa apa
apakabar apa kabar
apal hafal
apartement apartemen
apasih apa
apo apa
app aplikasi
apus hapus
aq aku
aquarius akuarius
arep harap
arrived sampai
aseli asli
asik asyik
asked bertanya
asking bertanya
assalammualaikum assalamualaikum
astagaaa astaga
at pada
atjeh aceh
atlit atlet
ato atau
attending menghadiri
atu satu
atw atau
aunty tante
ay sayang
aya ada
ayok ayo
ayoo ayo
ayooo ayo
ayoooo ayo
babies bayi-bayi
badmood suasana buruk
baek baik
bagaimanapun bagaimana pun
bai selamat tinggal
baiq baik
bakmie bakmi
baksos bakti sosial
balaikota balai kota
balek kembali
bales membalas
balinese orang bali
bambank bambang
bang ngab
banget sangat
bangett sangat
bangettt sangat
bangke bangkai
bangunin membangunkan
bantuin membantu
banyakin dibanyaki
bapakibu bapak ibu
baper bawa perasaan
baperan sensitif
barakallah semoga diberkahi allah
barengan bersama
barokah berkah
baruu baru
basecamp markas
bathin batin
batre baterai
batur teman
bayangin bayangkan
bb sayang
bbrp beberapa
bby sayang
bc karena
bcs karena
bct banyak omong
bday hari ulang tahun
beaches pantai-pantai
beautifull cantik
beb sayang
became menjadi
becanda bercanda
bedain membedakan
bedua berdua
began memulai
begins mulai
begitulah seperti itulah
beliin membelikan
belom belum
bem badan eksekutif mahasiswa
bener benar
beneran serius
beranih berani
berenti berhenti
beresin membereskan
berfikir berpikir
berflower berkembang
bestfriend sahabat
bestie sahabat
bete bosan total
betol betul
beud sekali
bg bang
bget sangat
bgitu begitu
bgmn bagaimana
bgni seperti ini
bgsd bangsat
bgst bangsat
bgt sangat
bgtt sangat
bgttt sangat
bgtu begitu
bgus bagus
bhs bahas
bhw bahwa
bikers pengendara sepeda
bikin membuat
biking bersepeda
bikinin buatkan
bilangin sampaikan
binggung bingung
birds burung-burung
bisaa bisa
bisaaa bisa
bkan bukan
bkin buat
bkn bukan
bks bekas
blang berkata
blessings anugerah
blg berkata
blh boleh
bli beli
blk kembali
blm belum
bln bulan
blom belum
bls balas
blum belum
bm pasar gelap
bner benar
bnget sangat
bngst bangsat
bngt sangat
bngun bangun
bnr benar
bntr sebentar
bnyak banyak
bnyk banyak
bocil bocah kecil
bodo bodoh
bodoamat masa bodoh
boemi bumi
bokap bapak
bokep film porno
boker buang air besar
boking memesan
books buku
boong bohong
bored bosan
bosen bosan
bosque bos
bosss bos
boutique butik
boyfriend pacar laki-laki
boys anak laki-laki
bp bapak
bpk bapak
br baru
brader saudara laki-laki
branding merek
brani berani
brapa berapa
brarti berarti
breaktime waktu istirahat
brg barang
brigjen brigadir jenderal
brings membawa
bro mas
brothers saudara laki-laki
brp berapa
brrti berarti
bru baru
bruh kawan
bs bisa
bsa bisa
bsk besok
bsok besok
bt kesal
bth butuh
btl betul
btw omong-omong
buatin buatkan
bucin budak cinta
buibu ibu-ibu
bukak buka
bukber buka bersama
bukittinggi bukit tinggi
bullshit kotoran sapi
bumil ibu hamil
bw bawa
bwt buat
by dengan
byk banyak
cafe kafe
caffe kafe
cai air
cakes kue
called dipanggil
calls telepon
cantiq cantik
cariin carikan
cars mobil-mobil
catering katering
cawapres calon wakil presiden
cb coba
celebrating merayakan
cemilan camilan
centre pusat
ceo direktur
cepet cepat
ceritain ceritakan
ceunah katanya
cewe perempuan
challenges tantangan
champions juara
changed berubah
changes merubah
changing mengganti
cheers bersulang
chinese cina
choices pilihan-pilihan
christ kristian
christian kristen
christmas natal
cinemas bioskop
ciwi perempuan
closing penutupan
clouds awan-awan
cm cuma
cntik cantik
cobain mencoba
cobak coba
coffe kopi
collab kolaborasi
colours warna
coment komentar
congrats selamat
congratulations selamat
couldnt tidak bisa
countries negara-negara
cowo cowok
cp capai
cpk capek
cpt cepat
cr cara
created dibuat
creating menciptakan
cs layanan pelanggan
cuan uang
cucok cocok
curug air terjun
cusss maju
customers pelanggan
cuz karena
cwe perempuan
cwek perempuan
cwo laki-laki
cwok laki-laki
cz karena
daan dan
dahh sudah
dann dan
dapet dapat
dapetin mendapatkan
dapoer dapur
darimana dari mana
daritadi dari tadi
dateng datang
dd adik
dearest tercinta
december desember
dech sudah
decor dekorasi
dehh deh
deket dekat
del hapus
denger dengar
dengerin mendengarkan
depe uang muka
depends bergantung
depo depot
dept departemen
des desember
deserves pantas
details detail
deui lagi
devils setan
dewe sendiri
dewek sendiri
dg dengan
dgn dengan
dgr dengar
dh sudah
dhe deh
diaa dia
diacara di acara
diajakin diajak
diakhir di akhir
diantara di antara
diaplikasi di aplikasi
diatas di atas
diawal di awal
dibawah di bawah
dibelakang di belakang
dibeliin dibelikan
dibidang di bidang
dibikin dibuat
dibilang dikatakan
didalam di dalam
didepan di depan
didunia di dunia
dies meninggal
difoto di foto
difotoin difoto
diguyur disiram
dih idih
dihari di hari
dijalan di jalan
dikala di kala
dikamar di kamar
dikampung di kampung
dikampus di kampus
dikantor di kantor
dikasih diberi
dikerjain dikerjakan
diklat pendidikan kilat
dikosan di kos
dikota di kota
dilain di lain
dilakuin dilakukan
dilapangan di lapangan
diliat dilihat
diliatin diperlihatkan
dilombok di lombok
diluar di luar
diluaran di luaran
dimalam di malam
dimana di mana
dimanapun di mana pun
dimasa di masa
dimasukin dimasukkan
dimata di mata
dimna dimana
dimuka di muka
dipagi di pagi
dipake dipakai
dipasaran di pasaran
dipikirin dipikirkan
dipinggir di pinggir
diposisi di posisi
dirumah di rumah
dirut direktur utama
disaat di saat
disana di sana
disebelah di sebelah
disekitar di sekitar
disekolah di sekolah
diseluruh di seluruh
disetiap di setiap
dishub dinas perhubungan
disini di sini
disitu di situ
ditahun di tahun
ditangan di tangan
ditanyain ditanyai
ditempat di tempat
ditengah di tengah
ditinggalin ditinggalkan
diubah dirubah
diujung di ujung
diwaktu di waktu
djakarta jakarta
dkt dekat
dl dulu
dlam dalam
dlm dalam
dlu dulu
dmn di mana
dmna di mana
dn dan
dng dengan
dngn dengan
doank saja
doh aduh
dolo dulu
dongg dong
donggg dong
dongs dong
donk dong
donut donat
donuts donat
doong dong
doors pintu
dpat dapat
dpc dewan pimpinan cabang
dpn depan
dps denpasar
dpt dapat
dr dari
drakor drama korea
dreams mimpi-mimpi
drg dokter gigi
dri dari
drinks minuman-minuman
drmh di rumah
drpd daripada
ds desa
dsini di sini
dsni di sini
dtg datang
duhh aduh
duhhh aduh
duluan mendahului
duluuu dulu
durung belum
duwe punya
dy dia
earlier lebih awal
editing mengedit
edt ubah
ee tahi
eek kotoran
elek jelek
emang memang
emg memang
emng emang
enakan lebih enak
endingnya akhirnya
ends berakhir
engga tidak
england inggris
english bahasa inggris
enjing pagi
enjoyed dinikmati
enk enak
entek habis
este es teh
eu eropa
eug saya
events acara
everythings semuanya
everytime setiap saat
ex eks
exercising olah raga
experiences pengalaman-pengalaman
eyes mata
faces muka
facts fakta
failed gagal
fak brengsek
fakenail kuku palsu
falls jatuh
families keluarga
familys keluarga
fams keluarga
fastpay pembayaran cepat
fathers para ayah
fav favorit
fave favorit
favourite favorit
featuring menampilkan
feb februari
february februari
feelings perasaan
feels merasa
feet kaki
feliz bahagia
fellas teman
fields bidang
fikir pikir
fikiran pikiran
fk fakultas kedokteran
flashback kilas balik
flek jerawat
flowers bunga
flyover jembatan layang
folks saudara-saudara
follback mengikuti kembali
foods makanan
fpi front pembela islam
french perancis
friday jumat
friends teman-teman
fruits buah
ft menampilkan
fuck tahi
fyi sekedar informasi
ga tidak
gaa tidak
gaada tidak ada
gabakal tidak akan
gabisa tidak bisa
gabole tidak boleh
gaboleh tidak boleh
gabut gaji buta
gaenak tidak enak
gaes kawan-kawan
gaess teman-teman
gaesss teman-teman
gais teman-teman
gajadi tidak jadi
gaje tidak jelas
gajelas tidak jelas
gak tidak
gakuat tidak kuat
galery galeri
galo galau
gamau tidak mau
gangerti tidak mengerti
gangguin ganggu
gank gang
gapake tidak pakai
gapapa tidak apa-apa
gaperlu tidak perlu
gapernah tidak pernah
gapunya tidak punya
gass gas
gasss gas
gassss gas
gasuka tidak suka
gatau tidak tahu
gatel gatal
gatsu gatot subroto
gausa tidak usah
gausah tidak usah
gawe bekerja
gblk goblok
gbu semoga tuhan memberkati
gedhe besar
geesss teman-teman
gegara gara-gara
gelem bersedia
gemes gemas
genk geng
genks kumpulan
gercep gerak cepat
germany jerman
gets mendapatkan
geus adalah
gf pacar
ghibah gosip
gilaaa gila
gilak gila
gile gila
gimana bagaimana
gimna bagaimana
gini begini
ginian seperti ini
girls para gadis
gitu begitu
gituu seperti itu
gituuu begitu
gives memberi
gk tidak
gmana bagaimana
gmn bagaimana
gmna bagaimana
gni seperti ini
go pergi
goals tujuan
goblog goblok
gods tuhan
gokil gila
gonna akan
goodbye selamat tinggal
goodbyes selamat tinggal
goodluck selamat berjuang
goodnight selamat malam
gosah tidak usah
gotta harus
gowes bersepeda
gpp tidak apa-apa
gr gara-gara
gracias terima kasih
grande besar
gratisan gratis
gratisss gratis
greatest terbaik
greetings salam
grgr gara-gara
gt begitu
gtu begitu
gua saya
gub gubernur
gue saya
gunungsari gunung sari
guys kawan-kawan
guyss teman-teman
guysss teman-teman
gw saya
gwa saya
gws semoga cepat sembuh
gx tidak
haaa ha
hadeh aduh
hadeuh aduh
haduh aduh
haii hai
haiii hai
hallo halo
haloo halo
halooo halo
halu halusinasi
hands tangan-tangan
happened terjadi
happens terjadi
happines kebahagiaan
hardcover sampul keras
hardest tersulit
hastag tagar
hatur terima
having memiliki
hayam ayam
hayoo ayo
hayooo ayo
hayu ayo
hayuk ayo
hbd selamat ulang tahun
hbis habis
hbs habis
heard didengar
hedon hedonisme
held ditahan
helped ditolong
helps tolong
hepi senang
heres di sini
heroes pahlawan
hes dia
heula dahulu
hiking mendaki
hitz populer
hiya hai
hj haji
hlm halaman
hmi himpunan mahasiswa indonesia
hny hanya
hnya hanya
hoaks informasi bohong
hoby hobi
hola halo
holidays hari libur
homes rumah
homestay rumah singgah
hometown kampung halaman
hopes harapan
hoping berharap
horison cakrawala
hq markas besar
hr hari
hrg harga
hri hari
hrs harus
hrus harus
hti hati
humans manusia
hurts menyakitkan
hy hai
hype sensasi
ibukota ibu kota
ideas ide-ide
idk tidak tahu
idr rupiah
idung hidung
idup hidup
ieu ini
if jika
ijin izin
ijo hijau
iki ini
iku itu
ikutin mengikuti
ilang hilang
im aku sedang
in di
including termasuk
indian orang india
indo indonesia
indonesias indonesia
inframe di dalam bingkai
inget ingat
inih ini
inii ini
iniii ini
insom insomnia
introducing memperkenalkan
invited diundang
ipk indeks prestasi kumulatif
ips ilmu pengetahuan sosial
ireng hitam
is adalah
islands pulau-pulau
isnt bukan
isok bisa
isteri istri
istighfar istigfar
istora istana olah raga
isuk besok
it itu
italian italia
itam hitam
items barang
itung hitung
itupun itu pun
ituu itu
ituuu itu
ive saya sudah
ja ya
jadian berhubungan
jadul jaman dulu
jagad jagat
jagain menjaga
jaim jaga penampilan
jak jakarta
jakpus jakarta pusat
jalanin menjalani
jan januari
jancok tahi
jang ujang
jannatul surga
january januari
japri jalur pribadi
jateng jawa tengah
jatim jawa timur
jatoh jatuh
jd jadi
jdi jadi
jelasin menjelaskan
jend jendral
jesus yesus
jg juga
jga juga
jgan jangan
jgn jangan
jingan bajingan
jkt jakarta
jl jalan
jln jalan
jm jam
jng jangan
jngan jangan
jngn jangan
jo jangan
jobs pekerjaan
jowo jawa
jr junior
jt juta
jugaa juga
jugaaa juga
jugak juga
jugo juga
jujurly jujur
july juli
jumping loncat
jw jiwa
jwb jawab
kab kabupaten
kabeh semua
kabid kepala bidang
kabupatenkota kabupaten kota
kadis kepala dinas
kaga tidak
kagak tidak
kakk kak
kakkk kak
kaks kak
kalbar kalimantan barat
kalean kalian
kalii kali
kalok kalau
kaltim kalimantan timur
kalu kalau
kambingbuntutdaging kambing buntut daging
kampoeng kampung
kamuu kamu
kamuuu kamu
kanaan kanan
kann kan
kannn kan
kanwil kantor wilayah
kao kau
karna karena
karoke karaoke
kasian kasihan
kasihhh kasih
katanya saya dengar
kayak seperti
kcp kecap
kdg kadang
kdrama drama korea
keatas ke atas
kebalik terbalik
kebanggan kebanggaan
kebangun terbangun
kebawah ke bawah
kebayang terbayang
kebelakang ke belakang
kebeli terbeli
kebuka terbuka
kec kecamatan
kedalam ke dalam
kedaton istana
kedepan ke depan
kedepannya ke depan
kedhai kedai
keempat ke empat
keeps tetap
kejepit terjepit
kel kelurahan
kelen kalian
keliatan kelihatan
keluarin keluarkan
kemana ke mana
kemaren kemarin
kenapaa kenapa
kene kita
kepengen ingin
kepikiran terpikir
kepoin ingin mengetahui
kerasa terasa
kerjaan pekerjaan
kerjain kerjakan
kerjasama kerja sama
kerjo kerja
kerumah ke rumah
kesana ke sana
keseringan terlalu sering
kesian kasihan
kesini ke sini
ketauan ketahuan
ketempat ke tempat
ketemuan bertemu
ketum ketua umum
keur sedang
kgn rindu
kid anak
kids anak-anak
kieu seperti ini
kinda agak
kings raja-raja
kirain saya kira
kitaa kita
kito kita
kitu begitu
kk kakak
kkk oke
kkkk oke
kkkkk oke
kl kalau
klau kalau
klean kalian
kliatan kelihatan
klo kalau
kls kelas
klu kalau
kluar keluar
kluarga keluarga
klw kalau
km kamu
kmaren kemarin
kmh bagaimana
kmn ke mana
kmna kemana
kmren kemarin
kmrin kemarin
kmrn kemarin
kmu kamu
knapa kenapa
knows tahu
knp kenapa
knpa mengapa
kntl kontol
ko mengapa
kocheng kucing
koffie kopi
kog kenapa
komen komentar
kominfo komunikasi dan informasi
komp komputer
komplek kompleks
kopdar kopi darat
koq mengapa
korean korea
kosan indekos
kost indekos
kotagede kota besar
kowe kamu
koyo koyok
kp kerja praktek
kpd kepada
kpn kapan
kppn kapan
kramat keramat
kraton keraton
kreatifitas kreativitas
krn karena
krna karena
ksh kasih
ksr kasar
kt kita
ktemu bertemu
ktmu bertemu
kulo saya
kumaha bagaimana
kurangi mengurangi
kuy ayo
kuyy yuk
kuyyy ayo
kw kualitas
kwalitas kualitas
ky seperti
kya seperti
kyak seperti
kyk seperti
kzl kesal
laen lain
lagii lagi
lagiii lagi
lagiiii lagi
lahiran melahirkan
lakuin lakukan
lalui melalui
lanjutin lanjutkan
lansung langsung
laper lapar
largest terbesar
latian latihan
lau kalau
lbh lebih
lbih lebih
leaders pemimpin
legends legenda
lemot lambat
lessons pelajaran
lets ayo
lewati melewati
lewatin melewati
lg lagi
lgi lagi
lgsg langsung
lgsung langsung
lheue lho
lhok lho
lhooo loh
liatin lihatkan
lies bohong
lifes kehidupan-kehidupan
lifestyle gaya hidup
lights lampu-lampu
liked disukai
lines garis
lips bibir
lives hidup
lngsng langsung
lngsung langsung
lo kamu
locals lokal
loe kamu
lohh lho
lohhh lho
lom belum
looks wajah
looo kamu
loved dicintai
lovers pasangan
loves mencintai
lp lupa
lsg langsung
lt lantai
lu kamu
luarbiasa luar biasa
lupain lupakan
lur teman-teman
luruuss lurus
luv cinta
luwih lebih
lwat lewat
maacih terima kasih
maafin maafkan
maap maaf
maapin maafkan
maba mahasiswa baru
mabar main bersama
mabok mabuk
macem macam
maem makan
maen main
maenan mainan
mager malas bergerak
mahasiswai mahasiswa
mahluk makhluk
mainin mainkan
makannya oleh sebab itu
makasar makassar
makasi terima kasih
makasih terima kasih
makasihh terima kasih
makasihhh terima kasih
makasii terima kasih
makes membuat
maksa memaksa
malem malam
males malas
malls mal
malming malam minggu
mamam makan
mancing memancing
maneh lagi
mangat semangat
manggil memanggil
manjah manja
mans laki-laki
mantab mantap
mantapp mantap
mantau memantau
manteman teman-teman
mantep mantap
mantul mantap sekali
maqna makna
marahin memarahi
masalalu masa lalu
masang memasang
masi masih
maskeran memakai masker
masok masuk
matahati mata hati
matiin matikan
matkul mata kuliah
matters berpengaruh
mauu mau
mauuu mau
mauuuu mau
max maksimal
mayan lumayan
mba mbak
mbaa mbak
mbaaa mbak
mbk mbak
mblo jomlo
mboh tidak mau
mboten tidak
mbuh belum
mcm macam
me aku
medsos sosial media
mee aku
meets menemui
meetup pertemuan
megang memegang
melow lembut
melu ikut
meluk memeluk
members anggota
memories kenangan
meneh tetap saja
mengcape lelah
menghianati mengkhianati
mengkezel kesal
mengkonsumsi mengonsumsi
mengmarah marah
mengpede percaya diri
mengsedih sedih
mengseneng senang
mensukseskan menyukseskan
mentri menteri
merci terima kasih
mergo karena
mesen pesan
metu keluar
mgkin mungkin
mgkn mungkin
mh mah
mhn mohon
midtown tengah kota
mikir berpikir
mikirin memikirkan
miles mil
milih memilih
milyar miliar
minjem pinjam
minutes menit
missed luput
mistakes kesalahan
mk maka
mkan makan
mkn makan
mksh terima kasih
mksih terima kasih
ml bercinta
mlaku berjalan
mlati melati
mls malas
mmg memang
mmng memang
mn mana
mngkin mungkin
mnjadi menjadi
mnta minta
mo mau
mom ibu
moms ibu-ibu
monday senin
monggo silakan
monitoring pengawasan
monmaap mohon maaf
mosok masa
mothers ibu-ibu
motong memotong
movies film-film
mreka mereka
mrk mereka
mrs ibu
ms yang benar
msh masih
msi masih
msih masih
msk masuk
mslh masalah
msuk masuk
mt mati
muaro muara
muke muka
mukul memukul
mulu melulu
mv video musik
mw mau
mz mas
nabrak menabrak
nabung menabung
naek naik
nagih menagih
naha kenapa
nahan menahan
nahh nah
naikturun naik turun
nails kuku
naksir menaksir
nambah menambah
nambahin menambahkan
nampol pukul
nanggung tanggung
nangis menangis
nanya bertanya
naon apa
nari menari
narik menarik
nasgor nasi goreng
nawarin menawarkan
nda tidak
ndak tidak
ndelok melihat
ndeso kampungan
ndk tidak
ndut gendut
ne ini
nego negosiasi
negri negeri
neh ini
nelpon menelepon
nemu menemukan
nemuin menemukan
nerima menerima
ngabisin menghabiskan
ngabuburit berkumpul
ngaca berkaca
ngadain mengadakan
ngadu mengadu
ngajak mengajak
ngajakin mengajak
ngajar mengajar
ngaji mengaji
ngak nggak
ngakak tertawa
ngaku mengaku
ngalah mengalah
ngalahin mengalahkan
ngalamin mengalami
ngambek marah
ngambil mengambil
ngampus ke kampus
nganggur menganggur
nganter mengantar
nganterin mengantar
ngantor ke kantor
ngantuk mengantuk
ngapa mengapa
ngapain sedang apa
ngarep berharap
ngaruh berpengaruh
ngasi memberi
ngasih memberi
ngatur mengatur
ngebacot banyak bicara
ngebayangin membayangkan
ngebuat membuat
ngechat mengirim pesan
ngedit mengedit
ngegas mengegas
ngeh sadar
ngejar mengejar
ngelakuin melakukan
ngelamar melamar
ngeliat melihat
ngeliatin melihat
ngelihat melihat
ngeluarin mengeluarkan
ngeluh mengeluh
ngepost memasang
ngerasa merasa
ngerjain mengerjakan
ngerokok merokok
ngerti mengerti
ngetik mengetik
ngetwit menulis cuitan
ngga tidak
nggak tidak
nggk tidak
nggo silahkan
ngikut mengikuti
ngikutin mengikuti
ngilang menghilang
ngilangin menghilangkan
nginap menginap
nginep menginap
ngintip mengintip
ngirim mengirim
ngisi mengisi
ngitung menghitung
ngk tidak
ngmg berbicara
ngoceh berbicara
ngombe minum
ngomong bicara
ngomongin membicarakan
ngono begitu
ngopi minum kopi
ngotot mengotot
ngucapin mengucapkan
ngulang mengulang
ngumpul berkumpul
ngundang mengundang
ngupi minum kopi
nich ini
nie ini
nihh ini
nihhh ini
niih ini
niii ini
nikahan pernikahan
nikmatin menikmati
ninggalin meninggalkan
nipu tipu
nite malam
nitip menitip
njing anjing
njirr anjing
nmr nomor
nnt nanti
nnti nanti
nntn nonton
nnton menonton
nobar nonton bareng
noh itu
nolak menolak
nongkrong menongkrong
nonton menonton
nontonin menonton
noob pemula
nopember november
notif notifikasi
nov november
ntar sebentar
ntn menonton
ntr sebentar
nulis menulis
numpak naik
numpang menumpang
nunggu menunggu
nungguin menunggu
nunjukin menunjukkan
nurut menurut
nutup menutup
nuwun terima kasih
nyadar tersadar
nyalahin menyalahkan
nyambung menyambung
nyampah menyampah
nyampe sampai
nyangkut menyangkut
nyantai bersantai
nyapa menyapa
nyari mencari
nyariin mencarikan
nyasar tersesat
nyebelin menyebalkan
nyebut menyebut
nyenengin menyenangkan
nyerah menyerah
nyesal menyesal
nyesek sesak
nyesel menyesal
nyet monyet
nyo ayo
nyoba mencoba
nyobain mencoba
nyokap ibu
nyuruh menyuruh
nyusul menyusul
oceans laut
oct oktober
october oktober
of dari
offers tawaran
oge juga
ojo jangan
okay oke
okeh oke
okey oke
omah rumah
ongkir ongkos kirim
ono ada
oom om
ootd pakaian hari ini
op operator
openbooking buka pemesanan
opened terbuka
opo apa
or atau
orangtua orang tua
orderan pesanan
org orang
ormas organisasi massa
orng orang
ortu orang tua
otewe dalam perjalanan
otw dalam perjalanan
oy hai
paan apa
padasuka pada suka
pagii pagi
pagiii pagi
pagiiii pagi
pahami memahami
paid dibayar
pait pahit
pake memakai
pakek pakai
paketan paket
pamutusan pemutusan
pantengin menunggui
pantes pantas
pantesan pantas saja
parents orang tua
parfume parfum
parpol partai politik
parts bagian
pasih apa sih
paslon pasangan calon
pc komputer
pcs satuan
pcx pak
pd pada
pda pada
pdahal padahal
pdhal padahal
pdhl padahal
pdt padat
pede percaya diri
pegi pergi
pemko pemerintah kota
pemprov pemerintah provinsi
pendikar pendekar
pengen ingin
peoples orang-orang
peptalk kata motivasi
perhatiin perhatikan
perna pernah
pesen pesan
pesenan pesanan
petrus pepet terus
pg pagi
pgi pagi
pgn ingin
photos foto-foto
php pemberi harapan palsu
pics gambar
pictures foto
pikirin pikirkan
pileg pilek
pilpres pemilihan presiden
pingin ingin
pinjem pinjam
pinter pintar
piro berapa
pisan juga
piye bagaimana
pjg panjang
pk pakai
pke pakai
pkl pukul
pku pusat kesehatan umum
places tempat-tempat
planning rencana
plans rencana-rencana
played dimainkan
players pemain
playing bermain
plg pulang
pliss tolong
plisss tolong
plng pulang
pls mohon
plt pelaksana tugas
pltu pembangkit listrik tenaga uap
pngen ingin
pnting penting
pny punya
pnya punya
points poin-poin
polwan polisi wanita
ponpes pondok pesantren
ponsel telepon selular
poto foto
ppsu penanganan sarana dan prasarana umum
pr pekerjaan rumah
prayers doa
presents hadiah
prewedding pranikah
pricelist daftar harga
prnah pernah
prnh pernah
problems masalah
products produk
proud bangga
prov provinsi
psti pasti
puede percaya diri
pulak pula
pulkam pulang kampung
pulo pulau
punten permisi
putera putra
qaqa kakak
qm kamu
qt kita
qta kita
qu ku
quando kapan
que kue
questions pertanyaan-pertanyaan
rabb allah
rakor rapat koordinasi
rangorang orang-orang
rapih rapi
rapopo tidak apa-apa
rasah rasa
realized tersadar
reasons alasan
records catatan
relaxing relaksasi
released dikeluarkan
req permintaan
residences tempat tinggal
resleting ritsleting
resorts resor
resto restoran
ribet rumit
ribs tulang rusuk
rights hak-hak
ringroad jalan lingkar
rm rumah makan
rmh rumah
rmv menghilangkan
roads jalan
roasted panggang
robb tuhan
roemah rumah
rooftop atap
rooms ruangan
roots akar
roso rasa
rp rupiah
rs rumah sakit
rsi rumah sakit islam
rsia rahasia
rsj rumah sakit jiwa
ruko rumah toko
rules peraturan
rumoh rumah
runners pelari
saay sayang
sadari menyadari
saha siapa
saia saya
saiki sekarang
salfok salah fokus
sallam salam
samaa sama
samaaa sama
samaan sama
samasama sama-sama
sambel sambal
samo hanya
sampe sampai
sampun sudah
samsek sama sekali
santuy santai
saos saus
sape siapa
sareng bersama
satay sate
satgas satuan tugas
satker satuan kerja
satnight malam minggu
satnite sabtu malam
satpol satuan polisi
saturday sabtu
says mengatakan
sbb maaf baru balas
sbg sebagai
sblm sebelum
sbnrnya sebenarnya
sby surabaya
sd sekolah dasar
sdah sudah
sdg sedang
sdh sudah
sdr saudara
se sih
seafood hidangan laut
seasons musim-musim
sebelom sebelum
sebenernya sebenarnya
sebrang seberang
seconds detik
sedi sedih
sedikitpun sedikit pun
segede sebesar
seger segar
segitu sebegitu
selow lambat
semalem semalam
semangatt semangat
semangattt semangat
sempet sempat
sempre pernah
semuaa semua
semuaaa semua
seneng senang
sensi sensitif
senyumin memberi senyum
serem seram
serie seri
seringkali sering kali
seriusan serius
seruuu seru
services layanan
sesok esok
sesuk besok
sgala segala
sgt sangat
shades corak
shalat salat
shared dibagi
sharing berbagi
shes dia
shit tahi
shoes sepatu
sholat salat
sholeh saleh
shopz toko-toko
shows menunjukkan
shubuh subuh
siapapun siapa pun
siapo siapa
siapp siap
siblings saudara
sich sih
sihh sih
siii sih
sik sebentar
simpen simpan
sintesa sintesis
sisters saudara perempuan
sj saja
sja saja
sk suka
ska suka
skali sekali
skalian sekalian
skarang sekarang
skg sekarang
skills keterampilan
skli sekali
skolah sekolah
skr sekarang
skrang sekarang
skrg sekarang
skrng sekarang
skuad geng
skuy ayo
skyline kaki langit
slalu selalu
slamat selamat
slamet selamat
slh salah
sll selalu
sllu selalu
slmt selamat
sm sama
smangat semangat
smbil sambil
smg semoga
smga semoga
smiles senyuman
smlm semalam
smo sampai
smoga semoga
smp sampai
smpai sampai
smpe sampai
smua semua
smuanya semuanya
sndiri sendiri
sndri sendiri
sni sini
so jadi
soale soalnya
soalnya karena
sodara saudara
soleh saleh
somay siomay
someones seseorang
songo sembilan
songs lagu-lagu
sooo jadi
sopo siapa
sosmed sosial media
soulmate belahan jiwa
souls jiwa
sounds suara
spam pesan sampah
spd sarjana pendidikan
sperti seperti
sprei seprai
sprti seperti
spt seperti
sr senior
srimping murung
st stasiun
stairs tangga
standards standar
standart standar
stars bintang-bintang
started dimulai
starts mulai
staying tinggal
stelah setelah
steps langkah
stiap setiap
stlh setelah
stories cerita
stronger lebih kuat
students murid-murid
styles gaya
su anjing
sucks payah
sukak suka
sulteng sulawesi tenggara
sulthan sultan
sultra sulawesi tenggara
sumsel sumatera selatan
sumut sumatera utara
sunday minggu
suport dukungan
supported didukung
suroboyo surabaya
surprised terkejut
suru suruh
sutera sutra
suwun terima kasih
sy saya
sya saya
syang sayang
syantik cantik
syg sayang
taik tahi
takan tak akan
takes mengambil
takkan tak akan
talks pembicaraan
tambahin menambahkan
tampol pukul
tanggungjawab tanggung jawab
taon tahun
taqwa takwa
taunya tahu-tahu
tauu tahu
tb tiba
tbh sejujurnya
tbtb tiba-tiba
td tadi
tdak tidak
tdi tadi
tdk tidak
tdr tidur
tdur tidur
teachers guru-guru
teams tim
tears air mata
tebel tebal
telfon telepon
teling memberitahu
tells memberi tahu
telp telepon
telponan menelpon
temans teman
temen teman
temples kuil
tenan betul
tengkyu terima kasih
terimakasi terima kasih
terimakasih terima kasih
teros terus
terosss terus
terraces teras
terupdate terbaru
teruss terus
terusss terus
testi kesaksian
tetep tetap
tetiba tiba-tiba
teuing tidak tahu
tgl tanggal
thankyou terima kasih
theatre teater
things situasi
thn tahun
thoughts pikiran
thousands ribuan
thru melalui
thursday kamis
thx terima kasih
tiati hati-hati
tickets tiket
tida tidak
tidor tidur
tinggalin tinggalkan
tjilik kecil
tkg tukang
tkp tempat kejadian perkara
tks terima kasih
tlah telah
tlg tolong
tlp telepon
tmn teman
tmpat tempat
tmpt tempat
tnggu tunggu
tnpa tanpa
todays hari ini
todos yang harus dilakukan
tools alat
toys mainan
tp tetapi
tpi tetapi
tq terima kasih
tracking mengikuti
travelling perjalanan
tresno cinta
trima terima
trimakasih terima kasih
trims terima kasih
trlalu terlalu
trs terus
trus terus
tsb tersebut
tshirt kaos
tt payudara
ttep tetap
ttg tentang
ttp tetap
tu itu
tuch itu
tuesday selasa
tuh itu
tuhh itu
tul betul
tumpa tumpah
tungguin menunggu
tunjukan tunjukkan
tuo tua
turu tidur
tuu itu
tw tahu
twins kembar
tyda tidak
ucapin ucapkan
ud sudah
udah sudah
udahan sudah
udahh udah
udeh sudah
udh sudah
uga juga
ujan hujan
ukhti saudara perempuan
ummah umat
unch gemas
und dan
unfaedah tidak berfaedah
untk untuk
unyu lucu
up atas
us kita
usa usah
using memakai
ustadz ustaz
utk untuk
uwu lucu
velg pelek
ver versi
vibe suasana
vibes suasana
vid video
videos video
views pandangan
visited berkunjung
vs melawan
wagub wakil gubernur
walks berjalan
walo walau
walopun walaupun
wani berani
wanna mau
wanted diinginkan
wants mau
warkop warung kopi
waroeng warung
waroenk warung
warong warung
wars perang
warunk warung
waterfalls air terjun
waters air
wednesday rabu
wedok perempuan
weeks berminggu-minggu
wellcome selamat datang
wes sudah
wetan timur
wheels roda
whos siapa
wiken akhir pekan
wilujeng selamat
wishes doa
wkt waktu
wktu waktu
wo woy
woii woi
woiii woi
women perempuan
womens perempuan-perempuan
words kata-kata
worlds dunia
wrna warna
xo mengirim ciuman
xtra ekstra
yaa ya
yaaa ya
yaaaa ya
yaallah ya allah
yaampun ya ampun
yall kalian semua
yasudah ya sudah
yaudah ya sudah
yeh ya
yg yang
yk ayo
yng yang
yo ya
yokk ayo
yoo ayo
yooo hai
youll kamu akan
youre kamu
youu kamu
yowes ya sudah
yth yang terhormat
yukk ayo
yukkk ayo
yuks ayo
yuu ayo
yuuk ayo
yuukk yuk
yuuu ayo
yuuuk ayo
zonk kosong
"""

_CORE_PAIRS = """
aja saja
banget sangat
bgt sangat
bikin membuat
blm belum
dgn dengan
dr dari
emang memang
ga tidak
gak tidak
gimana bagaimana
gini begini
gitu begitu
jgn jangan
kagak tidak
kayak seperti
km kamu
lo kamu
lu kamu
ngga tidak
nggak tidak
ntar sebentar
skrg sekarang
sy saya
tdk tidak
udah sudah
udh sudah
yg yang
"""


def _parse(text: str) -> Mapping[str, str]:
    table: dict[str, str] = {}
    for line in text.strip().splitlines():
        informal, formal = line.split(" ", 1)
        table[informal] = formal
    return MappingProxyType(table)


ID_COLLOQUIAL: Mapping[str, str] = _parse(_PAIRS)
ID_COLLOQUIAL_CORE: Mapping[str, str] = _parse(_CORE_PAIRS)
