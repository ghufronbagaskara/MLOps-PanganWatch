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

Pengambilan harga lengkap memerlukan 25 permintaan dan cuaca 10 permintaan sebelum retry. Batas eksekusi saat ini 30 permintaan untuk semua sumber dan retry; gunakan `--hanya harga` atau `--hanya cuaca` secara terpisah. Respons 403/429 dan CAPTCHA menghentikan proses. Jangan menjalankan ulang backfill untuk memeriksa tugas; gunakan arsip lokal.

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
python src/ingest_data.py --backfill        # tarikan awal sejak September 2024
python src/ingest_data.py --hanya harga     # pembaruan harga jika akses diizinkan
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
| LK-03 | Perancangan arsitektur pipeline data | Laporan lokal selesai |
| LK-04 | Implementasi *data ingestion* dan prapemrosesan | Kode, data, dan laporan lokal selesai |
| LK-05 | Versioning dataset menggunakan DVC | Dua versi dan laporan lokal selesai |
| LK-06 | Manajemen eksperimen menggunakan MLflow | Tiga run, registry kandidat, dan laporan lokal selesai |

## Lisensi

Berlisensi MIT. Rincian pada berkas [LICENSE](LICENSE).

Data harga berasal dari Pusat Informasi Harga Pangan Strategis Bank Indonesia. Data cuaca berasal
dari Open-Meteo dan tunduk pada lisensi Creative Commons Attribution 4.0 International.


## Versioning Dataset dengan DVC

Dataset hasil LK-04 dilacak melalui `data/raw.dvc` dan `data/processed.dvc`.
Commit `67a4f8d` mencatat versi pertama, sedangkan `a948b7f` menambahkan snapshot harga tiga baris.
Jumlah raw berubah dari dua menjadi tiga berkas. Tiga kunci sudah ada dalam arsip sehingga jumlah
observasi unik olahan tetap 13.825; perubahan waktu pengambilan tidak sama dengan harga baru.

```powershell
python -m dvc status
python -m dvc diff 67a4f8d a948b7f
```

Untuk snapshot baru yang sudah diperiksa, jalankan prapemrosesan lalu `python -m dvc add data/raw data/processed`.
Simpan penunjuk `.dvc` bersama versi kode setelah validasi. `dvc diff` membandingkan metadata berkas,
sehingga perubahan isi tabel perlu diperiksa terpisah. Jangan ulangi ingesti hanya untuk memeriksa tugas.

Remote `lokal` menunjuk penyimpanan lokal dan tidak tersedia otomatis di Codespaces lain.
Paket LK-05 menyertakan data, cache, dan Git bundle untuk reproduksi tanpa akses sumber data.

## Eksperimen dan Model Registry LK-06

`src/train.py` membentuk fitur per kota dan komoditas dari dataset harga LK-05.
Horizon 7 berarti tujuh observasi hari kerja. Tanggal target latih harus tidak melewati
batas periode latih; baris yang labelnya masuk periode uji dikeluarkan.
Parameter, metrik, hash dataset/kode, batas waktu, dan artefak model dicatat melalui MLflow.
Cuaca belum menjadi fitur. Evaluasi historis memakai snapshot terbaru dan belum membuktikan
semua revisi harga tersedia pada tanggal prediksi aslinya.

```powershell
python src/train.py --experiment panganwatch-lk06-terverifikasi --run-name lk06_baseline --n-estimators 300 --learning-rate 0.05 --num-leaves 31
python src/train.py --experiment panganwatch-lk06-terverifikasi --run-name lk06_dangkal_teregul --n-estimators 150 --learning-rate 0.03 --max-depth 4 --num-leaves 15
python src/train.py --experiment panganwatch-lk06-terverifikasi --run-name lk06_dalam_agresif --n-estimators 800 --learning-rate 0.1 --num-leaves 63
python -m mlflow ui --backend-store-uri ./mlruns --host 127.0.0.1 --port 5000
```

Tiga run terverifikasi tersimpan pada eksperimen `panganwatch-lk06-terverifikasi`.
Konfigurasi dangkal memiliki MAPE 5,7548 persen dan RMSE Rp5.403,15,
sedangkan naive memiliki MAPE 5,3746 persen dan RMSE Rp5.214,42.
Model terbaik di antara tiga LightGBM terdaftar sebagai `PanganWatch-Harga-Kandidat`, versi 1,
stage None. Kandidat belum mengungguli naive dan tidak dipromosikan ke produksi.
Pemilihan pada jendela uji yang sama bersifat eksploratif; evaluasi berikutnya membutuhkan
periode terpisah. Model tersimpan berhasil dimuat ulang dengan 1.050 prediksi identik.

## Pemeriksaan Paket Lokal

Seluruh 20 pengujian ingesti, prapemrosesan, dan pelatihan lulus. Kompilasi sintaks dan
`git diff --check` lulus. Ruff 0.17.0 sudah dijalankan; lint dan pemeriksaan format lulus.
Tidak ada permintaan baru ke PIHPS atau Open-Meteo pada penyelesaian LK-03 sampai LK-06.
Perubahan sesi ini masih lokal dan belum di-commit/push.
