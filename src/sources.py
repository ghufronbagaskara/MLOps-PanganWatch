"""Klien sumber data PanganWatch: PIHPS Bank Indonesia dan Open-Meteo.

Modul ini hanya mengambil data mentah dan mengembalikannya apa adanya.
Pembersihan dan pembentukan fitur dikerjakan di preprocess.py supaya kegagalan
jaringan tidak tercampur dengan kegagalan transformasi.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "sources.yaml"

log = logging.getLogger(__name__)


class SumberDataError(RuntimeError):
    """Permintaan ke sumber data gagal setelah seluruh percobaan ulang habis."""


@dataclass(frozen=True)
class Konfigurasi:
    pihps: dict[str, Any]
    open_meteo: dict[str, Any]
    backfill: dict[str, Any]

    @classmethod
    def muat(cls, path: Path = CONFIG_PATH) -> "Konfigurasi":
        with path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        return cls(pihps=raw["pihps"], open_meteo=raw["open_meteo"], backfill=raw["backfill"])


def _sesi(user_agent: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": user_agent, "Accept-Language": "id-ID,id;q=0.9"})
    return s


def _ambil_json(
    sesi: requests.Session,
    url: str,
    params: dict[str, Any],
    timeout: int,
    percobaan: int = 3,
    jeda_awal: float = 2.0,
) -> Any:
    """GET dengan backoff eksponensial.

    PIHPS kadang membalas 5xx ketika rentang tanggal yang diminta panjang, dan
    runner terjadwal harus tahan terhadap kegagalan sesaat seperti itu.
    """
    galat_terakhir: Exception | None = None
    for percobaan_ke in range(percobaan):
        try:
            r = sesi.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as exc:
            galat_terakhir = exc
            if percobaan_ke < percobaan - 1:
                jeda = jeda_awal * (2**percobaan_ke)
                log.warning("Permintaan gagal (%s), mencoba lagi dalam %.0f detik", exc, jeda)
                time.sleep(jeda)
    raise SumberDataError(f"Gagal mengambil {url}: {galat_terakhir}") from galat_terakhir


class KlienPIHPS:
    """Pembaca tabel harga PIHPS melalui endpoint JSON GetGridDataDaerah.

    Endpoint ini menyuplai dxDataGrid pada halaman Tabel Harga dan tidak
    berdokumen, sehingga nama parameternya diambil dari fungsi OnBeforeSend
    pada halaman tersebut.
    """

    USER_AGENT = "PanganWatch/1.0 (proyek akademik MLOps; kontak via repositori GitHub)"

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg
        self.sesi = _sesi(self.USER_AGENT)
        self.sesi.headers.update(
            {
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "https://www.bi.go.id/hargapangan/TabelHarga/PasarTradisionalDaerah",
            }
        )

    def ambil(
        self,
        comcat_id: str,
        province_id: int,
        regency_id: int,
        mulai: date,
        selesai: date,
    ) -> dict[str, str]:
        """Harga satu komoditas di satu kota, dikembalikan sebagai peta tanggal ke harga.

        Respons aslinya berbentuk pivot dengan tanggal sebagai kolom. Baris
        level 1 adalah kategori dan level 2 adalah komoditas; yang dipakai
        hanya level 2.
        """
        params = {
            "price_type_id": self.cfg["price_type_id"],
            "comcat_id": comcat_id,
            "province_id": province_id,
            "regency_id": regency_id,
            "market_id": "",
            "tipe_laporan": self.cfg["tipe_laporan"],
            "start_date": mulai.isoformat(),
            "end_date": selesai.isoformat(),
            "skip": 0,
            "take": 100,
            "requireTotalCount": "true",
        }
        data = _ambil_json(
            self.sesi,
            f"{self.cfg['base_url']}/GetGridDataDaerah",
            params,
            timeout=self.cfg["timeout_detik"],
        )
        baris = [b for b in data.get("data", []) if b.get("level") == 2]
        if not baris:
            return {}
        return {k: str(v) for k, v in baris[0].items() if "/" in k}

    def jeda(self) -> None:
        time.sleep(self.cfg["jeda_antar_permintaan_detik"])


class KlienOpenMeteo:
    USER_AGENT = "PanganWatch/1.0 (proyek akademik MLOps)"

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg
        self.sesi = _sesi(self.USER_AGENT)

    def arsip(self, lat: float, lon: float, mulai: date, selesai: date) -> dict[str, list]:
        data = _ambil_json(
            self.sesi,
            self.cfg["archive_url"],
            {
                "latitude": lat,
                "longitude": lon,
                "start_date": mulai.isoformat(),
                "end_date": selesai.isoformat(),
                "daily": ",".join(self.cfg["variabel_harian"]),
                "timezone": self.cfg["timezone"],
            },
            timeout=60,
        )
        return data["daily"]

    def prakiraan(self, lat: float, lon: float, hari: int = 16) -> dict[str, list]:
        data = _ambil_json(
            self.sesi,
            self.cfg["forecast_url"],
            {
                "latitude": lat,
                "longitude": lon,
                "daily": ",".join(self.cfg["variabel_harian"]),
                "forecast_days": hari,
                "timezone": self.cfg["timezone"],
            },
            timeout=60,
        )
        return data["daily"]
