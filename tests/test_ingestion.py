"""Uji batas akses dan penyimpanan ingestion tanpa permintaan jaringan."""

import sys
from datetime import date
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import ingest_data
import sources


def respons(status=200, text="", content_type="application/json"):
    r = Mock(status_code=status, text=text, headers={"Content-Type": content_type})
    r.json.return_value = {"data": []}
    if status >= 400:
        r.raise_for_status.side_effect = requests.HTTPError(str(status))
    return r


@pytest.mark.parametrize("status", [403, 429])
def test_blokir_tidak_dicoba_ulang(status, monkeypatch):
    sesi = Mock()
    sesi.get.return_value = respons(status)
    tidur = Mock()
    monkeypatch.setattr(sources.time, "sleep", tidur)
    with pytest.raises(sources.AksesDihentikan):
        sources._ambil_json(sesi, "https://example.invalid", {}, 1, anggaran=[3])
    assert sesi.get.call_count == 1
    tidur.assert_not_called()


def test_captcha_memerlukan_pemeriksaan_manual(monkeypatch):
    sesi = Mock()
    sesi.get.return_value = respons(
        text="<html>CAPTCHA</html>", content_type="text/html"
    )
    tidur = Mock()
    monkeypatch.setattr(sources.time, "sleep", tidur)
    with pytest.raises(sources.AksesDihentikan, match="manual"):
        sources._ambil_json(sesi, "https://example.invalid", {}, 1)
    assert sesi.get.call_count == 1
    tidur.assert_not_called()


def test_retry_menghabiskan_anggaran_actual(monkeypatch):
    sesi = Mock()
    sesi.get.side_effect = requests.Timeout()
    monkeypatch.setattr(sources.time, "sleep", Mock())
    anggaran = [2]
    with pytest.raises(sources.AksesDihentikan, match="Anggaran"):
        sources._ambil_json(sesi, "https://example.invalid", {}, 1, anggaran=anggaran)
    assert sesi.get.call_count == 2
    assert anggaran == [0]


def test_anggaran_dibagi_antar_client():
    cfg = sources.Konfigurasi.muat()
    anggaran = [1]
    harga = sources.KlienPIHPS(cfg.pihps, anggaran)
    cuaca = sources.KlienOpenMeteo(cfg.open_meteo, anggaran)
    harga.sesi = Mock()
    harga.sesi.get.return_value = respons()
    cuaca.sesi = Mock()
    harga.ambil("com_16", 16, 43, date(2026, 10, 1), date(2026, 10, 2))
    with pytest.raises(sources.AksesDihentikan):
        cuaca.prakiraan(-7.8, 112.0)
    cuaca.sesi.get.assert_not_called()


def test_blokir_tidak_dilewati_ke_pasangan_berikutnya(monkeypatch):
    cfg = sources.Konfigurasi.muat()
    ambil = Mock(side_effect=sources.AksesDihentikan("HTTP 403"))
    monkeypatch.setattr(sources.KlienPIHPS, "ambil", ambil)
    with pytest.raises(sources.AksesDihentikan):
        ingest_data.tarik_harga(cfg, date(2026, 10, 1), date(2026, 10, 2))
    assert ambil.call_count == 1


def test_simpan_tidak_menimpa_berkas_lama(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest_data, "RAW", tmp_path)
    monkeypatch.setattr(ingest_data, "stempel", lambda: "20261014T120000")
    p = ingest_data.simpan(pd.DataFrame({"harga": [10000]}), "harga_pihps")
    isi_lama = p.read_bytes()
    with pytest.raises(FileExistsError):
        ingest_data.simpan(pd.DataFrame({"harga": [20000]}), "harga_pihps")
    assert p.read_bytes() == isi_lama


def test_jendela_arsip_harian_valid(monkeypatch):
    cfg = sources.Konfigurasi.muat()
    monkeypatch.setattr(sources.Konfigurasi, "muat", lambda: cfg)
    monkeypatch.setattr(
        ingest_data,
        "rentang",
        lambda args, cfg: (date(2026, 10, 11), date(2026, 10, 14)),
    )
    tarik = Mock(return_value=pd.DataFrame())
    monkeypatch.setattr(ingest_data, "tarik_cuaca", tarik)
    monkeypatch.setattr(ingest_data, "simpan", Mock())
    assert ingest_data.main(["--hanya", "cuaca"]) == 0
    assert tarik.call_args.args[1:3] == (date(2026, 10, 6), date(2026, 10, 9))
