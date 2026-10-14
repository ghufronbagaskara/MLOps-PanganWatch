"""Prapemrosesan data mentah PanganWatch menjadi tabel panjang siap olah.

    python src/preprocess.py

Membaca seluruh berkas pada data/raw/, menggabungkannya, lalu menulis satu
berkas Parquet ke data/processed/. Ketika satu kunci muncul pada lebih dari
satu tarikan, nilai dari berkas terbaru yang dipakai, karena PIHPS masih
merevisi harga satu hari setelah tanggal survei.
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

import pandas as pd
import pandera.pandas as pa
from pandera.pandas import Column, DataFrameSchema

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"

log = logging.getLogger("preprocess")

# Harga eceran per kilogram di luar rentang ini hampir pasti salah baca,
# bukan gejolak pasar. Beras terendah pernah di Rp11.000, cabai tertinggi Rp150.000.
HARGA_MIN, HARGA_MAKS = 5_000, 300_000

SKEMA = DataFrameSchema(
    {
        "tanggal": Column("datetime64[ns]", nullable=False),
        "kota": Column(str, nullable=False),
        "komoditas": Column(str, nullable=False),
        "harga": Column(
            float, pa.Check.in_range(HARGA_MIN, HARGA_MAKS), nullable=False
        ),
        "is_imputed": Column(bool, nullable=False),
        "ditarik_pada": Column("datetime64[ns]", nullable=False),
    },
    unique=["tanggal", "kota", "komoditas"],
    strict=True,
)


def stempel_dari_nama(path: Path) -> pd.Timestamp:
    m = re.search(r"_(\d{8}T\d{6})\.csv$", path.name)
    if not m:
        raise ValueError(f"Nama berkas tanpa stempel waktu: {path.name}")
    return pd.to_datetime(m.group(1), format="%Y%m%dT%H%M%S")


def baca_harga_mentah() -> pd.DataFrame:
    berkas = sorted(RAW.glob("harga_pihps_*.csv"))
    if not berkas:
        raise FileNotFoundError(
            f"Tidak ada berkas harga di {RAW}. Jalankan ingest_data.py dahulu."
        )

    bagian = []
    for p in berkas:
        df = pd.read_csv(p)
        df["ditarik_pada"] = stempel_dari_nama(p)
        bagian.append(df)
        log.info("Membaca %s (%d baris)", p.name, len(df))
    return pd.concat(bagian, ignore_index=True)


def bersihkan(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["tanggal"] = pd.to_datetime(df["tanggal"], format="%d/%m/%Y", errors="coerce")

    # PIHPS memakai titik sebagai pemisah ribuan pada beberapa halaman dan koma
    # pada halaman lain, sementara sel kosong ditulis sebagai tanda hubung.
    teks = df["harga_mentah"].astype(str).str.strip()
    teks = teks.replace({"-": None, "": None, "nan": None, "None": None})
    df["harga"] = pd.to_numeric(
        teks.str.replace(r"[.,]", "", regex=True), errors="coerce"
    )

    sebelum = len(df)
    df = df.dropna(subset=["tanggal", "harga"])
    log.info("Membuang %d baris tanpa tanggal atau harga valid", sebelum - len(df))

    di_luar = ~df["harga"].between(HARGA_MIN, HARGA_MAKS)
    if di_luar.any():
        log.warning(
            "Membuang %d baris dengan harga di luar rentang wajar", int(di_luar.sum())
        )
        df = df[~di_luar]

    # Revisi harga membuat satu kunci muncul berkali-kali; tarikan terbaru menang.
    df = (
        df.sort_values("ditarik_pada")
        .drop_duplicates(subset=["tanggal", "kota", "komoditas"], keep="last")
        .reset_index(drop=True)
    )
    return df[["tanggal", "kota", "komoditas", "harga", "ditarik_pada"]]


def lengkapi_hari_kerja(df: pd.DataFrame) -> pd.DataFrame:
    """Isi hari kerja yang kosong dengan harga terakhir, ditandai is_imputed.

    PIHPS tidak mensurvei pada akhir pekan dan hari libur nasional. Kekosongan
    akhir pekan dibiarkan apa adanya, tetapi hari kerja yang bolong diisi maju
    agar perhitungan lag tidak melompati tanggal tanpa disadari model.
    """
    hasil = []
    for (kota, komoditas), grup in df.groupby(["kota", "komoditas"], sort=False):
        grup = grup.set_index("tanggal").sort_index()
        hari_kerja = pd.bdate_range(grup.index.min(), grup.index.max())
        penuh = grup.reindex(hari_kerja)
        penuh["is_imputed"] = penuh["harga"].isna()
        penuh[["harga", "ditarik_pada"]] = penuh[["harga", "ditarik_pada"]].ffill()
        penuh["kota"] = kota
        penuh["komoditas"] = komoditas
        hasil.append(penuh.rename_axis("tanggal").reset_index())

    gabungan = pd.concat(hasil, ignore_index=True)
    gabungan["harga"] = gabungan["harga"].astype(float)
    return gabungan[
        ["tanggal", "kota", "komoditas", "harga", "is_imputed", "ditarik_pada"]
    ]


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    df = lengkapi_hari_kerja(bersihkan(baca_harga_mentah()))
    SKEMA.validate(df, lazy=True)

    PROCESSED.mkdir(parents=True, exist_ok=True)
    keluaran = PROCESSED / "harga_harian.parquet"
    df.to_parquet(keluaran, index=False)

    terisi = int((~df["is_imputed"]).sum())
    log.info("Tersimpan %s", keluaran.name)
    log.info(
        "%d baris, %d deret, %s sampai %s",
        len(df),
        df.groupby(["kota", "komoditas"]).ngroups,
        df["tanggal"].min().date(),
        df["tanggal"].max().date(),
    )
    log.info(
        "Hasil observasi %d baris, hasil pengisian %d baris (%.1f persen)",
        terisi,
        len(df) - terisi,
        (len(df) - terisi) / len(df) * 100,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
