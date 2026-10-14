# PanganWatch

Sistem peringatan dini lonjakan harga pangan berbasis *continual learning*. Proyek ini meramalkan
harga eceran harian lima komoditas pangan strategis pada lima kota untuk horizon satu sampai tujuh
hari ke depan, kemudian menerjemahkan hasil ramalan tersebut menjadi level peringatan bagi
pengambil keputusan pengadaan.

Repositori ini merupakan artefak mata kuliah Machine Learning Operations (CIF60050), Program Studi
S1 Teknik Informatika, Fakultas Ilmu Komputer, Universitas Brawijaya. Isinya bertambah secara
bertahap mengikuti rangkaian Lembar Kerja, mulai dari penyiapan infrastruktur sampai pemantauan
model pada tahap produksi.

## Latar Belakang Singkat

Harga komoditas pangan bergejolak di Indonesia bergerak dengan amplitudo yang besar. Pada rentang
September 2024 sampai September 2026, harga cabai rawit merah di Kota Malang bergerak antara
Rp20.750 dan Rp108.750 per kilogram. Rata-rata kuartalannya melompat dari Rp34.534 pada kuartal
keempat 2024 menjadi Rp72.203 pada kuartal pertama 2025. Pergeseran sebesar itu membuat model yang
dilatih sekali lalu dibiarkan akan kehilangan relevansinya dalam hitungan bulan. Oleh karena itu,
sistem ini dirancang sejak awal dengan pipeline *continuous training* yang dipicu oleh jadwal,
degradasi performa, deteksi *drift*, dan penanda kalender.

## Sumber Data

| Sumber | Peran | Cara akses | Frekuensi |
| --- | --- | --- | --- |
| PIHPS Bank Indonesia | Variabel target dan fitur *lag* | Endpoint JSON `GetGridDataDaerah`, tanpa kunci | Hari kerja |
| Open-Meteo | Variabel eksogen cuaca sentra produksi | REST API terbuka, tanpa kunci | Arsip harian dan prakiraan 16 hari |

Keduanya dapat diakses tanpa biaya. Open-Meteo membatasi pemakaian gratis pada 10.000 panggilan per
hari, sedangkan kebutuhan harian sistem ini 16 panggilan.

## Struktur Direktori

Susunan folder mengikuti konvensi *Cookiecutter Data Science* agar pemisahan antara data mentah,
data olahan, kode sumber, dan artefak model tetap tegas.

```
MLOps-PanganWatch/
├── .devcontainer/
│   └── devcontainer.json     definisi lingkungan Codespaces
├── .github/
│   └── workflows/            workflow GitHub Actions
├── config/                   berkas konfigurasi sumber data dan parameter
├── data/
│   ├── raw/                  hasil tarikan mentah, tidak pernah disunting
│   ├── interim/              hasil antara proses pembersihan
│   └── processed/            tabel fitur siap latih
├── docs/                     dokumentasi teknis dan diagram arsitektur
├── models/                   artefak model terlatih
├── notebooks/                eksplorasi dan analisis data
├── src/
│   └── panganwatch/          paket Python utama
├── tests/                    pengujian otomatis
├── .gitignore
├── LICENSE
├── README.md
└── requirements.txt
```

Folder `data/` dan `models/` sengaja tidak dilacak Git. Git tidak dirancang untuk menyimpan berkas
biner berukuran besar, dan setiap tarikan harian akan menambah riwayat yang tidak perlu. Kedua
folder tersebut akan dilacak menggunakan DVC pada tahap berikutnya, sehingga Git hanya menyimpan
berkas penunjuk berukuran kecil.

## Menjalankan Lingkungan Pengembangan

### GitHub Codespaces

Lingkungan pengembangan proyek ini didefinisikan sebagai kode pada
[`.devcontainer/devcontainer.json`](.devcontainer/devcontainer.json). Siapa pun yang membuka
repositori ini akan memperoleh lingkungan yang identik, sehingga persoalan klasik *works on my
machine* tidak muncul.

1. Buka halaman repositori di GitHub.
2. Tekan tombol **Code**, pilih tab **Codespaces**, lalu **Create codespace on main**.
3. Tunggu proses pembangunan kontainer selesai. Perintah `postCreateCommand` akan memasang seluruh
   dependensi pada `requirements.txt` secara otomatis.
4. Verifikasi lingkungan melalui terminal:

```bash
python --version
pip list | grep -E "mlflow|dvc|pandas"
```

Kontainer berbasis `mcr.microsoft.com/devcontainers/python:1-3.11-bookworm` dengan Python 3.11.
Ekstensi Python, Pylance, Jupyter, Ruff, GitLens, dan DVC dipasang otomatis. Port 5000 dan 8000
diteruskan untuk antarmuka MLflow dan layanan API inferensi.

### Lingkungan Lokal

```bash
git clone https://github.com/ghufronbagaskara/MLOps-PanganWatch.git
cd MLOps-PanganWatch
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Menjalankan Pipeline Data

### Penarikan Data

```bash
python src/ingest_data.py --backfill        # tarikan awal sejak September 2024
python src/ingest_data.py                   # tarikan harian berjalan
python src/ingest_data.py --hanya cuaca     # batasi pada satu sumber
```

Setiap eksekusi menulis berkas baru bernama `<sumber>_<stempel waktu>.csv` ke `data/raw/`, misalnya
`harga_pihps_20261014T132540.csv`. Berkas lama tidak pernah tertimpa, sehingga dua tarikan yang
berurutan dapat dibandingkan dan revisi harga yang terbit belakangan tetap terekam.

Mode harian menarik jendela tiga hari ke belakang, bukan hanya hari berjalan. PIHPS masih mengoreksi
entri survei satu hari setelah tanggal pengukuran, dan jendela tumpang tindih tersebut memastikan
koreksi itu ikut tertangkap. Kegagalan pada satu pasangan komoditas dan kota dicatat sebagai
peringatan, tidak menggugurkan seluruh batch, karena tarikan berikutnya akan menambalnya.

### Prapemrosesan

```bash
python src/preprocess.py
```

Skrip tersebut membaca seluruh berkas pada `data/raw/`, lalu mengerjakan empat hal. Pertama,
membongkar bentuk pivot PIHPS yang menempatkan tanggal sebagai kolom menjadi tabel panjang dengan
satu baris per kombinasi tanggal, kota, dan komoditas. Kedua, mengurai angka harga yang ditulis
sebagai teks bertanda pemisah ribuan serta membuang sel kosong. Ketiga, menyelesaikan duplikasi
akibat jendela tumpang tindih dengan memenangkan nilai dari tarikan terbaru. Keempat, mengisi hari
kerja yang kosong secara maju dan menandainya pada kolom `is_imputed` agar model dapat membedakan
harga hasil observasi dari harga hasil pengisian.

Hasilnya divalidasi dengan skema Pandera sebelum ditulis ke `data/processed/harga_harian.parquet`.
Batch yang melanggar skema ditolak, sehingga data yang cacat tidak pernah sampai ke tahap pelatihan.

### Pengujian

```bash
python -m pytest tests -q
```

## Strategi Percabangan

Proyek ini menerapkan GitHub Flow. Branch `main` dijaga selalu berada pada kondisi yang dapat
dijalankan, dan seluruh perubahan masuk melalui *pull request*.

```bash
git checkout -b feat/nama-pekerjaan
git add .
git commit -m "feat: ringkasan perubahan"
git push -u origin feat/nama-pekerjaan
gh pr create --fill
```

Penamaan branch mengikuti pola `<tipe>/<ringkasan>` dengan tipe `feat`, `fix`, `docs`, atau
`chore`. Pesan commit mengikuti konvensi *Conventional Commits*. Branch dihapus setelah *pull
request* digabungkan.

## Peta Lembar Kerja

| LK | Cakupan | Status |
| --- | --- | --- |
| LK-01 | Inisiasi proyek, strategi *continual learning*, kriteria keberhasilan | Selesai |
| LK-02 | Repositori, Codespaces, struktur direktori, GitHub Flow | Selesai |
| LK-03 | Perancangan arsitektur pipeline data | Belum |
| LK-04 | Implementasi *data ingestion* dan prapemrosesan | Belum |
| LK-05 | Versioning dataset menggunakan DVC | Belum |
| LK-06 | Manajemen eksperimen menggunakan MLflow | Belum |

## Lisensi

Berlisensi MIT. Rincian pada berkas [LICENSE](LICENSE).

Data harga berasal dari Pusat Informasi Harga Pangan Strategis Bank Indonesia. Data cuaca berasal
dari Open-Meteo dan tunduk pada lisensi Creative Commons Attribution 4.0 International.
