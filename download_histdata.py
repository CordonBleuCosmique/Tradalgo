#!/usr/bin/env python3
"""
Téléchargement automatique des données EURUSD M1 depuis Histdata.com
puis resampling en H1 et export au format Histdata CSV.

Usage :
    python download_histdata.py                     # 2020-2024
    python download_histdata.py --start 2019 --end 2024
    python download_histdata.py --output sample_data/EURUSD_H1.csv
"""
from __future__ import annotations
import argparse
import os
import sys
import zipfile
import tempfile
from pathlib import Path

import pandas as pd


# ── Vérification des dépendances ─────────────────────────────────────────────
try:
    import histdata
    from histdata.api import Platform, TimeFrame
    HAS_HISTDATA = True
except ImportError:
    HAS_HISTDATA = False


def download_year(year: int, tmp_dir: Path) -> pd.DataFrame | None:
    """Télécharge et décompresse les données M1 Histdata pour une année."""
    if not HAS_HISTDATA:
        print("  ✗ Package 'histdata' manquant : pip install histdata")
        return None

    print(f"  Téléchargement {year}...", end=" ", flush=True)
    try:
        # Télécharge dans le dossier temporaire
        old_cwd = os.getcwd()
        os.chdir(tmp_dir)
        histdata.download(
            pair="eurusd",
            year=str(year),
            platform=Platform.METATRADER,
            time_frame=TimeFrame.ONE_MINUTE,
        )
        os.chdir(old_cwd)

        # Trouve le fichier ZIP téléchargé
        zips = list(tmp_dir.glob("*.zip"))
        if not zips:
            print("✗ Aucun ZIP trouvé")
            return None

        # Extrait le CSV
        with zipfile.ZipFile(zips[0]) as zf:
            csv_files = [f for f in zf.namelist() if f.endswith(".csv")]
            if not csv_files:
                print("✗ Pas de CSV dans le ZIP")
                return None
            with zf.open(csv_files[0]) as f:
                # Format Histdata M1 : YYYYMMDD HHMMSS;O;H;L;C;V
                df = pd.read_csv(
                    f, sep=";", header=None,
                    names=["datetime", "Open", "High", "Low", "Close", "Volume"],
                )
        df["datetime"] = pd.to_datetime(df["datetime"], format="%Y%m%d %H%M%S")
        df = df.set_index("datetime")
        df.index = df.index.tz_localize("UTC")
        print(f"✓ {len(df):,} barres M1")

        # Nettoyage
        for z in zips:
            z.unlink()

        return df

    except Exception as e:
        os.chdir(old_cwd)
        print(f"✗ Erreur : {e}")
        return None


def resample_to_h1(m1: pd.DataFrame) -> pd.DataFrame:
    """Resampling M1 → H1."""
    h1 = m1.resample("1h").agg({
        "Open":   "first",
        "High":   "max",
        "Low":    "min",
        "Close":  "last",
        "Volume": "sum",
    }).dropna(subset=["Open"])
    h1 = h1[h1.index.dayofweek < 5]   # supprime les barres weekend
    return h1


def save_histdata_csv(df: pd.DataFrame, path: Path) -> None:
    """Sauvegarde au format Histdata H1 : YYYYMMDD HHMMSS;O;H;L;C;V"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for ts, row in df.iterrows():
            f.write(
                f"{ts.strftime('%Y%m%d %H%M%S')};"
                f"{row['Open']:.5f};{row['High']:.5f};"
                f"{row['Low']:.5f};{row['Close']:.5f};"
                f"{int(row['Volume'])}\n"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Téléchargement EURUSD Histdata.com → H1 CSV")
    parser.add_argument("--start", type=int, default=2020, help="Première année (défaut: 2020)")
    parser.add_argument("--end",   type=int, default=2024, help="Dernière année incluse (défaut: 2024)")
    parser.add_argument("--output", default="sample_data/EURUSD_H1_real.csv",
                        help="Chemin de sortie CSV")
    args = parser.parse_args()

    if not HAS_HISTDATA:
        print("Erreur : installez d'abord le package histdata :")
        print("  pip install histdata")
        sys.exit(1)

    years = list(range(args.start, args.end + 1))
    print(f"Téléchargement EURUSD H1 — {years[0]} → {years[-1]}")
    print(f"Sortie : {args.output}\n")

    all_frames: list[pd.DataFrame] = []

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for year in years:
            df_m1 = download_year(year, tmp_path)
            if df_m1 is not None:
                df_h1 = resample_to_h1(df_m1)
                all_frames.append(df_h1)
                print(f"    → {len(df_h1):,} barres H1")

    if not all_frames:
        print("\nAucune donnée téléchargée.")
        sys.exit(1)

    merged = pd.concat(all_frames).sort_index()
    merged = merged[~merged.index.duplicated(keep="first")]

    out = Path(args.output)
    save_histdata_csv(merged, out)

    size_mb = out.stat().st_size / 1_048_576
    atr_pips = (merged["High"] - merged["Low"]).mean() / 0.0001

    print(f"\n{'='*50}")
    print(f"  Fichier    : {out}")
    print(f"  Barres H1  : {len(merged):,}")
    print(f"  Période    : {merged.index[0].date()} → {merged.index[-1].date()}")
    print(f"  ATR moyen  : {atr_pips:.1f} pips")
    print(f"  Taille     : {size_mb:.1f} MB")
    print(f"{'='*50}")
    print(f"\nPour lancer le backtest :")
    print(f"  python run_backtest.py --source histdata_csv \\")
    print(f"      --csv {out} \\")
    print(f"      --start {args.start}-01-01 --end {args.end + 1}-01-01 --equity 10000")


if __name__ == "__main__":
    main()
