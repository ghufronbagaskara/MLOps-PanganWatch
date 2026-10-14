"""Uji fitur per deret dan batas waktu label peramalan."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from train import bangun_fitur, evaluasi, kolom_fitur, pisah_waktu


def deret():
    tanggal = pd.bdate_range("2026-01-01", periods=100)
    return pd.DataFrame(
        {
            "tanggal": list(tanggal) * 2,
            "kota": ["Malang"] * 100 + ["Surabaya"] * 100,
            "komoditas": ["Cabai"] * 200,
            "harga": np.concatenate(
                [10_000 + np.arange(100) * 100, 20_000 + np.arange(100) * 200]
            ),
            "is_imputed": False,
            "ditarik_pada": pd.Timestamp("2026-06-01"),
        }
    )


def test_label_tujuh_observasi_dan_purge_tidak_masuk_uji():
    f = bangun_fitur(deret(), 7)
    latih, uji, batas = pisah_waktu(f, 30)
    assert (latih["tanggal_target"] <= batas).all()
    assert latih["tanggal_target"].max() < uji["tanggal"].min()
    assert ((latih["tanggal_target"] - latih["tanggal"]).dt.days > 7).all()
    assert not (set(latih.index) & set(uji.index))
    assert len(latih) + len(uji) < len(f)


def test_fitur_tidak_berubah_jika_harga_masa_depan_diubah():
    df = deret()
    awal = bangun_fitur(df, 7)
    batas = df["tanggal"].iloc[60]
    df.loc[df["tanggal"] > batas, "harga"] *= 5
    setelah = bangun_fitur(df, 7)
    fitur = kolom_fitur(awal)
    pd.testing.assert_frame_equal(
        awal.loc[awal["tanggal"] <= batas, fitur],
        setelah.loc[setelah["tanggal"] <= batas, fitur],
    )
    assert "tanggal_target" not in fitur
    assert not {"target", "target_harga", "harga_t0"}.intersection(fitur)


def test_lag_tidak_mencampur_kota():
    df = deret()
    gabung = bangun_fitur(df, 7)
    sendiri = bangun_fitur(df[df["kota"] == "Malang"], 7)
    kolom = [c for c in gabung if c.startswith("rel_")]
    pd.testing.assert_frame_equal(
        gabung.loc[gabung["kota"] == "Malang", kolom].reset_index(drop=True),
        sendiri[kolom].reset_index(drop=True),
    )


def test_metrik_naive_dan_arah_hanya_hari_bergerak():
    skor = evaluasi(
        np.array([100.0, 120.0]), np.array([100.0, 110.0]), np.array([100.0, 100.0])
    )
    assert skor["mae"] == 5
    assert skor["rmse"] == pytest.approx(np.sqrt(50))
    assert skor["mape"] == pytest.approx(100 / 24)
    assert skor["akurasi_arah"] == 100
    datar = evaluasi(np.array([100.0]), np.array([101.0]), np.array([100.0]))
    assert np.isnan(datar["akurasi_arah"])


def test_horizon_dan_jendela_uji_invalid_ditolak():
    with pytest.raises(ValueError, match="Horizon"):
        bangun_fitur(deret(), 0)
    f = bangun_fitur(deret(), 7)
    with pytest.raises(ValueError, match="Jendela"):
        pisah_waktu(f, 0)
    with pytest.raises(ValueError, match="tidak cukup"):
        pisah_waktu(f, 500)
