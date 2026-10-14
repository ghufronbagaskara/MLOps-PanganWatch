"""Pengujian logika prapemrosesan yang tidak sepele: parsing, revisi, dan pengisian."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from preprocess import bersihkan, lengkapi_hari_kerja, stempel_dari_nama


def _mentah(rows: list[tuple[str, str, str, str, str]]) -> pd.DataFrame:
    return pd.DataFrame(
        rows, columns=["tanggal", "kota", "komoditas", "harga_mentah", "ditarik_pada"]
    ).astype({"ditarik_pada": "datetime64[ns]"})


def test_pemisah_ribuan_koma_dan_titik_dibaca_sama():
    df = bersihkan(
        _mentah(
            [
                ("01/09/2026", "Malang", "Cabai", "23,700", "2026-09-01T15:00:00"),
                ("02/09/2026", "Malang", "Cabai", "23.700", "2026-09-01T15:00:00"),
            ]
        )
    )
    assert df["harga"].tolist() == [23700.0, 23700.0]


def test_sel_kosong_dan_tanda_hubung_dibuang():
    df = bersihkan(
        _mentah(
            [
                ("01/09/2026", "Malang", "Cabai", "-", "2026-09-01T15:00:00"),
                ("02/09/2026", "Malang", "Cabai", "", "2026-09-01T15:00:00"),
                ("03/09/2026", "Malang", "Cabai", "30,000", "2026-09-01T15:00:00"),
            ]
        )
    )
    assert len(df) == 1
    assert df.iloc[0]["harga"] == 30000.0


def test_harga_di_luar_rentang_wajar_dibuang():
    df = bersihkan(
        _mentah(
            [
                ("01/09/2026", "Malang", "Beras", "900", "2026-09-01T15:00:00"),
                ("02/09/2026", "Malang", "Beras", "15,550", "2026-09-01T15:00:00"),
            ]
        )
    )
    assert df["harga"].tolist() == [15550.0]


def test_revisi_harga_memenangkan_tarikan_terbaru():
    """Tanggal yang sama ditarik dua kali; nilai dari tarikan belakangan yang dipakai."""
    df = bersihkan(
        _mentah(
            [
                ("01/09/2026", "Malang", "Cabai", "40,000", "2026-09-01T15:00:00"),
                ("01/09/2026", "Malang", "Cabai", "41,500", "2026-09-02T15:00:00"),
            ]
        )
    )
    assert len(df) == 1
    assert df.iloc[0]["harga"] == 41500.0


def test_hari_kerja_bolong_diisi_dan_ditandai():
    """Rabu 2 September 2026 hilang, sehingga harus terisi maju dari Selasa."""
    bersih = bersihkan(
        _mentah(
            [
                ("01/09/2026", "Malang", "Cabai", "40,000", "2026-09-05T15:00:00"),
                ("03/09/2026", "Malang", "Cabai", "42,000", "2026-09-05T15:00:00"),
            ]
        )
    )
    penuh = lengkapi_hari_kerja(bersih).sort_values("tanggal").reset_index(drop=True)

    assert len(penuh) == 3
    assert penuh.loc[1, "tanggal"] == pd.Timestamp("2026-09-02")
    assert penuh.loc[1, "harga"] == 40000.0
    assert penuh.loc[1, "is_imputed"]
    assert not penuh.loc[0, "is_imputed"]


def test_akhir_pekan_tidak_ikut_diisi():
    bersih = bersihkan(
        _mentah(
            [
                ("04/09/2026", "Malang", "Cabai", "40,000", "2026-09-08T15:00:00"),
                ("07/09/2026", "Malang", "Cabai", "41,000", "2026-09-08T15:00:00"),
            ]
        )
    )
    penuh = lengkapi_hari_kerja(bersih)
    assert penuh["tanggal"].dt.dayofweek.max() < 5
    assert len(penuh) == 2


def test_nama_berkas_tanpa_stempel_ditolak():
    with pytest.raises(ValueError):
        stempel_dari_nama(Path("harga_pihps.csv"))
