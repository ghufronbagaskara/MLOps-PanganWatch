# PanganWatch

Sistem peringatan dini lonjakan harga pangan berbasis *continual learning*. Proyek ini dirancang untuk meramalkan harga eceran lima komoditas pada lima kota dan menghasilkan peringatan bagi pengambil keputusan pengadaan. Implementasi sampai LK-06 berupa pipeline harga, versioning DVC, serta tiga eksperimen LightGBM untuk horizon tujuh observasi hari kerja. Sistem peringatan produksi dan integrasi fitur cuaca belum diterapkan.

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

Keduanya dapat diakses tanpa biaya. Pengambilan harga lengkap memerlukan 25 permintaan dan cuaca
10 permintaan, sedangkan satu eksekusi dibatasi 30 permintaan termasuk percobaan ulang. Oleh karena
itu, kedua sumber dijalankan terpisah melalui `--hanya harga` dan `--hanya cuaca`. Respons 403, 429,
atau halaman CAPTCHA langsung menghentikan proses agar layanan sumber tidak terus dibebani. Backfill
cukup dijalankan sekali; pembaruan berikutnya memakai mode harian yang jauh lebih ringan.

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
│   ├── ingest_data.py        penarikan snapshot
│   ├── preprocess.py         validasi tabel harga
│   └── sources.py            klien sumber data
├── tests/                    pengujian otomatis
├── .gitignore
├── LICENSE
├── README.md
└── requirements.txt
```

Isi dataset pada `data/raw/` dan `data/processed/` dilacak DVC. Artefak model dicatat MLflow. Git tidak dirancang untuk menyimpan berkas
biner berukuran besar, dan setiap tarikan harian akan menambah riwayat yang tidak perlu. Git menyimpan penunjuk `data/raw.dvc` dan `data/processed.dvc`, sedangkan byte dataset berada pada cache DVC dan penyimpanan data.

## Menjalankan Lingkungan Pengembangan

### GitHub Codespaces

Lingkungan pengembangan proyek ini didefinisikan sebagai kode pada
[`.devcontainer/devcontainer.json`](.devcontainer/devcontainer.json). Definisi ini menyamakan lingkungan dasar dan daftar dependensi. Reproduksi identik tetap membutuhkan versi data dan versi pustaka yang sama.

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
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Menjalankan Pipeline Data

### Penarikan Data

```bash
python src/ingest_data.py --backfill                     # tarikan awal sejak September 2024
python src/ingest_data.py --hanya harga                  # pembaruan harian, tiga hari ke belakang
python src/ingest_data.py --hanya cuaca                  # pembaruan cuaca sentra produksi
python src/ingest_data.py --hanya harga --sejak 2026-10-15   # mengejar tanggal yang terlewat
python src/ingest_data.py --hanya harga --kota Malang    # batasi cakupan kota atau komoditas
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

## Versioning Dataset dengan DVC

### Mengapa Dataset Perlu Diberi Versi

Model peramalan pada proyek ini merupakan hasil dari tiga masukan sekaligus, yaitu kode, konfigurasi,
dan data. Git sudah mencatat dua masukan pertama. Namun, dataset bertambah setiap hari kerja dan harga
lama dapat direvisi oleh sumber, sehingga dataset yang dipakai melatih model hari ini tidak akan sama
dengan dataset satu bulan kemudian. Tanpa versi data, metrik pada MLflow tidak dapat diulang, karena
tidak ada cara untuk memperoleh kembali baris yang benar-benar digunakan saat pelatihan.

DVC menyimpan berkas data pada remote dan hanya menitipkan berkas penunjuk `.dvc` berisi hash md5 ke
Git. Satu commit Git dengan demikian mengikat versi kode dan versi data secara bersamaan. Ketika model
perlu diaudit atau penurunan performa perlu ditelusuri, commit tersebut cukup dipulihkan dan `dvc pull`
mengembalikan data yang identik hingga tingkat byte. Pemisahan tersebut juga menjaga ukuran repositori
Git tetap kecil, karena arsip CSV dan Parquet tidak masuk ke riwayat Git.

### Riwayat Versi

| Versi | Commit | Isi | Baris olahan | Berkas mentah |
| --- | --- | --- | ---: | ---: |
| 1 | `67a4f8d` | Backfill 2 September 2024 sampai 14 Oktober 2026 | 13.825 | 2 |
| 2 | `a948b7f` | Tarikan ulang cabai rawit merah Malang, 12 sampai 14 Oktober | 13.825 | 3 |
| 3 | `c3d83c3` | Lima komoditas Malang, 15 sampai 19 Oktober | 13.840 | 4 |

Versi 2 menambah tiga baris mentah, tetapi ketiga tanggalnya sudah tercakup pada versi 1. Prapemrosesan
memilih tarikan terbaru untuk setiap kunci, sehingga yang berubah hanya kolom `ditarik_pada`. Versi 3
menambah 15 observasi unik pada tanggal yang belum pernah tercatat. Perbedaan kedua versi tersebut
menunjukkan bahwa pertambahan berkas mentah belum tentu berarti pertambahan observasi.

### Alur Menambah Versi

```bash
python src/ingest_data.py --hanya harga --kota Malang --sejak 2026-10-15
python src/preprocess.py
python -m dvc status                     # DVC mendeteksi perubahan isi direktori
python -m dvc add data/raw data/processed
python -m dvc diff                       # bandingkan dengan versi pada HEAD
git add data/raw.dvc data/processed.dvc
git commit -m "data: ringkasan isi versi baru"
python -m dvc push
```

Untuk memulihkan versi lama, pilih commit yang sesuai lalu ambil datanya:

```bash
git checkout a948b7f -- data/raw.dvc data/processed.dvc
python -m dvc checkout
```

Remote `lokal` menunjuk direktori `../.dvc-storage` pada komputer pengembang. Pada Codespaces atau
komputer lain, remote tersebut perlu diganti dengan penyimpanan bersama seperti S3 atau MinIO melalui
`dvc remote add -d`.

## Eksperimen dan Model Registry

`src/train.py` membentuk fitur per pasangan kota dan komoditas, lalu melatih LightGBM untuk meramalkan
perubahan harga relatif. Parameter, metrik, hash dataset, hash kode, dan artefak model dicatat ke MLflow
pada setiap eksekusi. Horizon 7 berarti tujuh observasi hari kerja. Baris latih yang tanggal targetnya
jatuh pada periode uji dikeluarkan agar label uji tidak bocor ke data latih.

```bash
python src/train.py --experiment panganwatch-lk06-terverifikasi --run-name lk06_baseline --n-estimators 300 --learning-rate 0.05 --num-leaves 31
python src/train.py --experiment panganwatch-lk06-terverifikasi --run-name lk06_dangkal_teregul --n-estimators 150 --learning-rate 0.03 --max-depth 4 --num-leaves 15
python src/train.py --experiment panganwatch-lk06-terverifikasi --run-name lk06_dalam_agresif --n-estimators 800 --learning-rate 0.1 --num-leaves 63
python -m mlflow ui --backend-store-uri ./mlruns --host 127.0.0.1 --port 5000
```

| Konfigurasi | RMSE (Rp) | MAPE (%) |
| --- | ---: | ---: |
| Baseline | 5.645,60 | 6,06 |
| Dangkal teratur | 5.403,15 | 5,75 |
| Dalam agresif | 6.169,38 | 6,70 |
| Naive | 5.214,42 | 5,37 |

Konfigurasi dangkal teratur menghasilkan galat terendah di antara tiga LightGBM dan terdaftar pada
Model Registry sebagai `PanganWatch-Harga-Kandidat` versi 1. Ketiga konfigurasi masih kalah dari
model naive, sehingga kandidat tersebut belum dipromosikan ke tahap produksi. Eksperimen ini memakai
dataset versi 2; hash dataset pada setiap run memastikan perbandingan dilakukan pada data yang sama.

## Peta Lembar Kerja

| LK | Cakupan | Status |
| --- | --- | --- |
| LK-01 | Inisiasi proyek, strategi *continual learning*, kriteria keberhasilan | Selesai |
| LK-02 | Repositori, Codespaces, struktur direktori, GitHub Flow | Selesai |
| LK-03 | Perancangan arsitektur pipeline data | Selesai |
| LK-04 | Implementasi *data ingestion* dan prapemrosesan | Selesai |
| LK-05 | Versioning dataset menggunakan DVC | Selesai |
| LK-06 | Manajemen eksperimen menggunakan MLflow | Selesai |

## Lisensi

Berlisensi MIT. Rincian pada berkas [LICENSE](LICENSE).

Data harga berasal dari Pusat Informasi Harga Pangan Strategis Bank Indonesia. Data cuaca berasal
dari Open-Meteo dan tunduk pada lisensi Creative Commons Attribution 4.0 International.
