"""Penarikan data harian PanganWatch dari PIHPS dan Open-Meteo.

Dua mode penggunaan:

    python src/ingest_data.py --backfill          tarikan awal 24 bulan
    python src/ingest_data.py                     tarikan harian berjalan

Setiap eksekusi menulis berkas baru bernama <sumber>_<stempel waktu>.csv ke
data/raw/ dan tidak pernah menimpa berkas lama, sehingga tarikan berikutnya
dapat dibandingkan dengan tarikan sebelumnya.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from sources import Konfigurasi, KlienOpenMeteo, KlienPIHPS, SumberDataError

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"

log = logging.getLogger("ingest")


def stempel() -> str:
    return datetime.now().strftime("%Y%m%dT%H%M%S")


def tarik_harga(cfg: Konfigurasi, mulai: date, selesai: date) -> pd.DataFrame:
    klien = KlienPIHPS(cfg.pihps)
    baris: list[dict] = []
    gagal = 0

    for kota in cfg.pihps["kota"]:
        for kom in cfg.pihps["komoditas"]:
            try:
                deret = klien.ambil(
                    kom["comcat_id"], kota["province_id"], kota["regency_id"], mulai, selesai
                )
            except SumberDataError as exc:
                # Satu pasangan yang gagal tidak boleh menggugurkan seluruh batch;
                # jendela tumpang tindih pada tarikan berikutnya akan menambalnya.
                log.error("Lewati %s di %s: %s", kom["nama"], kota["nama"], exc)
                gagal += 1
                continue

            for tgl, harga in deret.items():
                baris.append(
                    {
                        "tanggal": tgl,
                        "kota": kota["nama"],
                        "komoditas": kom["nama"],
                        "harga_mentah": harga,
                        "sumber": "pihps",
                    }
                )
            log.info("%-14s %-22s %4d titik", kota["nama"], kom["nama"], len(deret))
            klien.jeda()

    if gagal:
        log.warning("%d pasangan gagal ditarik pada eksekusi ini", gagal)
    return pd.DataFrame(baris)


def tarik_cuaca(cfg: Konfigurasi, mulai: date, selesai: date) -> pd.DataFrame:
    klien = KlienOpenMeteo(cfg.open_meteo)
    variabel = cfg.open_meteo["variabel_harian"]
    baris: list[dict] = []

    for s in cfg.open_meteo["sentra"]:
        harian = klien.arsip(s["lat"], s["lon"], mulai, selesai)
        for i, tgl in enumerate(harian["time"]):
            rec = {"tanggal": tgl, "sentra": s["nama"], "jenis": "arsip"}
            rec.update({v: harian[v][i] for v in variabel})
            baris.append(rec)

        prakiraan = klien.prakiraan(s["lat"], s["lon"])
        for i, tgl in enumerate(prakiraan["time"]):
            rec = {"tanggal": tgl, "sentra": s["nama"], "jenis": "prakiraan"}
            rec.update({v: prakiraan[v][i] for v in variabel})
            baris.append(rec)

        log.info("%-10s %3d hari arsip, %3d hari prakiraan", s["nama"],
                 len(harian["time"]), len(prakiraan["time"]))

    return pd.DataFrame(baris)


def simpan(df: pd.DataFrame, prefiks: str) -> Path:
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / f"{prefiks}_{stempel()}.csv"
    df.to_csv(path, index=False)
    log.info("Tersimpan %s (%d baris)", path.name, len(df))
    return path


def rentang(args: argparse.Namespace, cfg: Konfigurasi) -> tuple[date, date]:
    hari_ini = date.today()
    if args.backfill:
        return date.fromisoformat(str(cfg.backfill["tanggal_mulai"])), hari_ini
    return hari_ini - timedelta(days=cfg.backfill["jendela_harian_hari"]), hari_ini


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Penarik data harga dan cuaca PanganWatch.")
    p.add_argument("--backfill", action="store_true",
                   help="tarik seluruh rentang sejak tanggal_mulai pada konfigurasi")
    p.add_argument("--hanya", choices=["harga", "cuaca"], help="batasi pada satu sumber saja")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")
    cfg = Konfigurasi.muat()
    mulai, selesai = rentang(args, cfg)
    log.info("Rentang penarikan %s sampai %s", mulai, selesai)

    if args.hanya != "cuaca":
        harga = tarik_harga(cfg, mulai, selesai)
        if harga.empty:
            log.error("Tidak ada satu pun baris harga yang berhasil ditarik")
            return 1
        simpan(harga, "harga_pihps")

    if args.hanya != "harga":
        # Arsip ERA5 terbit tertinggal beberapa hari, jadi ujung rentangnya dimundurkan.
        batas = selesai - timedelta(days=cfg.open_meteo["jeda_arsip_hari"])
        simpan(tarik_cuaca(cfg, mulai, batas), "cuaca_openmeteo")

    return 0


if __name__ == "__main__":
    sys.exit(main())
