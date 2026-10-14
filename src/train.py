"""Pelatihan model peramalan harga PanganWatch dengan pelacakan MLflow.

    python src/train.py --horizon 7 --n-estimators 400 --learning-rate 0.05
    python src/train.py --run-name lgbm_dangkal --max-depth 4

Satu eksekusi menghasilkan satu run MLflow berisi parameter, metrik, dan
artefak model. Metrik dilaporkan terpisah per komoditas karena skala harga
beras dan cabai berbeda jauh, sehingga angka gabungan menyesatkan.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import mlflow
import mlflow.lightgbm
import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "processed" / "harga_harian.parquet"
MODELS = ROOT / "models"

log = logging.getLogger("train")

LAG = [1, 3, 7, 14, 30]
ROLL = [7, 30]


def bangun_fitur(df: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Susun tabel fitur dengan kebenaran point-in-time.

    Seluruh lag dan statistik rolling dihitung di dalam satu deret
    (kota, komoditas). Menghitungnya lintas deret akan membocorkan harga kota
    lain ke dalam fitur dan membuat metrik validasi terlihat jauh lebih baik
    daripada performa sebenarnya.

    Target dan fitur dinyatakan sebagai perubahan relatif terhadap harga hari
    ini, bukan rupiah mutlak. Beras bergerak di kisaran Rp15.000 sementara
    cabai menembus Rp100.000, sehingga satu model atas harga mutlak akan
    didominasi komoditas berskala besar dan galatnya tidak sebanding antar
    deret. Pada ruang relatif, baseline naive setara dengan menebak nol.
    """
    if horizon < 1:
        raise ValueError("Horizon harus minimal satu observasi hari kerja.")
    bagian = []
    for _, g in df.groupby(["kota", "komoditas"], sort=False):
        g = g.sort_values("tanggal").copy()
        h = g["harga"]
        sebelumnya = h.shift(1)

        for n in LAG:
            g[f"rel_lag_{n}"] = sebelumnya / h.shift(n) - 1
        for w in ROLL:
            rata = sebelumnya.rolling(w).mean()
            g[f"rel_roll_mean_{w}"] = sebelumnya / rata - 1
            g[f"rel_roll_std_{w}"] = sebelumnya.rolling(w).std() / rata
            g[f"rel_roll_min_{w}"] = sebelumnya / sebelumnya.rolling(w).min() - 1
            g[f"rel_roll_max_{w}"] = sebelumnya / sebelumnya.rolling(w).max() - 1
        g["momentum_7"] = sebelumnya.pct_change(7)
        g["volatilitas_30"] = sebelumnya.pct_change().rolling(30).std()

        g["harga_t0"] = h
        g["target_harga"] = h.shift(-horizon)
        g["tanggal_target"] = g["tanggal"].shift(-horizon)
        g["target"] = h.shift(-horizon) / h - 1
        bagian.append(g)

    f = pd.concat(bagian, ignore_index=True)
    f["dow"] = f["tanggal"].dt.dayofweek
    f["bulan"] = f["tanggal"].dt.month
    f["kota_kode"] = f["kota"].astype("category").cat.codes
    f["komoditas_kode"] = f["komoditas"].astype("category").cat.codes
    return f.dropna(
        subset=["target", f"rel_lag_{max(LAG)}", f"rel_roll_std_{max(ROLL)}"]
    )


def kolom_fitur(f: pd.DataFrame) -> list[str]:
    buang = {
        "tanggal",
        "kota",
        "komoditas",
        "harga",
        "ditarik_pada",
        "harga_t0",
        "target",
        "target_harga",
        "tanggal_target",
    }
    return [c for c in f.columns if c not in buang]


def pisah_waktu(
    f: pd.DataFrame, uji_hari: int
) -> tuple[pd.DataFrame, pd.DataFrame, pd.Timestamp]:
    if uji_hari < 1:
        raise ValueError("Jendela uji harus minimal satu hari kalender.")
    batas = f["tanggal"].max() - pd.Timedelta(days=uji_hari)
    # Label latih harus sudah tersedia sebelum jendela uji dimulai.
    latih = f[f["tanggal_target"] <= batas]
    uji = f[f["tanggal"] > batas]
    if latih.empty or uji.empty:
        raise ValueError("Data tidak cukup untuk menghasilkan bagian latih dan uji.")
    return latih, uji, batas


def mape(y: np.ndarray, p: np.ndarray) -> float:
    return float(np.mean(np.abs((y - p) / y)) * 100)


AMBANG_GERAK = 0.005


def arah(y: np.ndarray, p: np.ndarray, t0: np.ndarray) -> float:
    """Persentase prediksi yang menebak arah pergerakan harga dengan benar.

    Hari yang harganya praktis tidak bergerak dikeluarkan dari perhitungan.
    Beras dan telur kerap bertahan pada angka yang sama persis selama sepekan,
    sehingga sign(y - t0) bernilai nol sementara prediksi hampir tidak pernah
    tepat nol. Tanpa penyaringan ini, deret yang datar selalu terhitung salah
    dan menekan metrik jauh di bawah lemparan koin.
    """
    bergerak = np.abs(y - t0) / t0 > AMBANG_GERAK
    if not bergerak.any():
        return float("nan")
    return float(
        np.mean(
            np.sign(y[bergerak] - t0[bergerak]) == np.sign(p[bergerak] - t0[bergerak])
        )
        * 100
    )


def evaluasi(y: np.ndarray, p: np.ndarray, t0: np.ndarray) -> dict[str, float]:
    galat = y - p
    return {
        "mae": float(np.mean(np.abs(galat))),
        "rmse": float(np.sqrt(np.mean(galat**2))),
        "mape": mape(y, p),
        "akurasi_arah": arah(y, p, t0),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Latih model peramalan harga dan catat ke MLflow."
    )
    p.add_argument(
        "--horizon", type=int, default=7, help="jumlah observasi hari kerja ke depan"
    )
    p.add_argument("--n-estimators", type=int, default=300)
    p.add_argument("--learning-rate", type=float, default=0.05)
    p.add_argument("--max-depth", type=int, default=-1)
    p.add_argument("--num-leaves", type=int, default=31)
    p.add_argument(
        "--uji-hari",
        type=int,
        default=60,
        help="panjang jendela uji dalam hari kalender",
    )
    p.add_argument("--run-name", default=None)
    p.add_argument("--experiment", default="panganwatch-harga")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    if not DATA.exists():
        log.error("%s belum ada. Jalankan preprocess.py terlebih dahulu.", DATA)
        return 1

    df = pd.read_parquet(DATA)
    f = bangun_fitur(df, args.horizon)
    fitur = kolom_fitur(f)

    latih, uji, batas = pisah_waktu(f, args.uji_hari)
    log.info(
        "Latih %d baris sampai %s, uji %d baris sesudahnya",
        len(latih),
        batas.date(),
        len(uji),
    )

    nama = (
        args.run_name
        or f"lgbm_h{args.horizon}_n{args.n_estimators}_lr{args.learning_rate}"
    )
    mlflow.set_tracking_uri(
        os.environ.get("MLFLOW_TRACKING_URI", (ROOT / "mlruns").as_uri())
    )
    mlflow.set_experiment(args.experiment)
    revisi = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()

    with mlflow.start_run(
        run_name=nama,
        tags={"task": "regresi", "sumber": "pihps", "split": "purged_time_split"},
    ):
        mlflow.log_params(
            {
                "horizon": args.horizon,
                "n_estimators": args.n_estimators,
                "learning_rate": args.learning_rate,
                "max_depth": args.max_depth,
                "num_leaves": args.num_leaves,
                "uji_hari": args.uji_hari,
                "jumlah_fitur": len(fitur),
                "baris_latih": len(latih),
                "baris_uji": len(uji),
                "horizon_unit": "observasi_hari_kerja",
                "split_cutoff": batas.isoformat(),
                "target_latih_terakhir": latih["tanggal_target"].max().isoformat(),
                "uji_awal": uji["tanggal"].min().isoformat(),
                "target_uji_terakhir": uji["tanggal_target"].max().isoformat(),
                "data_sha256": hashlib.sha256(DATA.read_bytes()).hexdigest(),
                "source_sha256": hashlib.sha256(
                    Path(__file__).read_bytes()
                ).hexdigest(),
                "git_commit": revisi,
                "git_dirty": bool(
                    subprocess.run(
                        ["git", "status", "--porcelain"],
                        cwd=ROOT,
                        capture_output=True,
                        text=True,
                        check=False,
                    ).stdout.strip()
                ),
                "ambang_gerak": AMBANG_GERAK,
            }
        )

        model = LGBMRegressor(
            n_estimators=args.n_estimators,
            learning_rate=args.learning_rate,
            max_depth=args.max_depth,
            num_leaves=args.num_leaves,
            random_state=42,
            verbose=-1,
        )
        model.fit(latih[fitur], latih["target"])

        t0 = uji["harga_t0"].to_numpy()
        y = uji["target_harga"].to_numpy()
        # Model meramalkan perubahan relatif, lalu dikembalikan ke rupiah.
        pred = t0 * (1 + model.predict(uji[fitur]))

        skor = evaluasi(y, pred, t0)
        # Baseline naive: harga h hari ke depan diasumsikan sama dengan hari ini.
        skor_naive = evaluasi(y, t0, t0)
        skor["skill_score_vs_naive"] = (
            1 - skor["rmse"] / skor_naive["rmse"]
            if skor_naive["rmse"] > 0
            else float("nan")
        )

        mlflow.log_metrics(skor)
        mlflow.log_metrics(
            {f"naive_{k}": v for k, v in skor_naive.items() if k != "akurasi_arah"}
        )

        for kom, g in uji.assign(pred=pred).groupby("komoditas"):
            m = evaluasi(
                g["target_harga"].to_numpy(),
                g["pred"].to_numpy(),
                g["harga_t0"].to_numpy(),
            )
            kunci = kom.lower().replace(" ", "_")
            mlflow.log_metric(f"mape__{kunci}", m["mape"])
            mlflow.log_metric(f"arah__{kunci}", m["akurasi_arah"])

        penting = (
            pd.DataFrame({"fitur": fitur, "bobot": model.feature_importances_})
            .sort_values("bobot", ascending=False)
            .reset_index(drop=True)
        )

        with tempfile.TemporaryDirectory() as tmp:
            p_imp = Path(tmp) / "feature_importance.csv"
            penting.to_csv(p_imp, index=False)
            mlflow.log_artifact(str(p_imp))

            p_pred = Path(tmp) / "prediksi_uji.csv"
            uji.assign(prediksi=pred)[
                [
                    "tanggal",
                    "tanggal_target",
                    "kota",
                    "komoditas",
                    "harga_t0",
                    "target_harga",
                    "prediksi",
                ]
            ].to_csv(p_pred, index=False)
            mlflow.log_artifact(str(p_pred))

        mlflow.lightgbm.log_model(model, "model", input_example=uji[fitur].head(3))

        MODELS.mkdir(exist_ok=True)
        (MODELS / "metrik_terakhir.json").write_text(
            json.dumps({"run": nama, **skor}, indent=2), encoding="utf-8"
        )

        log.info(
            "MAPE %.2f%% | RMSE %.0f | arah %.1f%% | skill vs naive %.3f",
            skor["mape"],
            skor["rmse"],
            skor["akurasi_arah"],
            skor["skill_score_vs_naive"],
        )
        log.info("Lima fitur terpenting: %s", ", ".join(penting["fitur"].head(5)))

    return 0


if __name__ == "__main__":
    sys.exit(main())
