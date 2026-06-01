#!/usr/bin/env python3
"""
Téléchargement automatique des données EURUSD M1 depuis Histdata.com
puis resampling en H1 et export au format Histdata CSV.

Usage :
    python download_histdata.py                      # 2019-2024
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


# ── Détection de l'API histdata (plusieurs versions coexistent) ──────────────
def _get_download_fn():
    """Renvoie la fonction de téléchargement histdata disponible, ou None."""
    # Tentative 1 : API philipperemy >= 0.1.4
    try:
        from histdata import download_hist_data       # noqa: PLC0415
        from histdata.api import Platform, TimeFrame  # noqa: PLC0415

        def _dl(pair, year, tmp_dir):
            old = os.getcwd()
            os.chdir(tmp_dir)
            try:
                download_hist_data(
                    year=str(year),
                    pair=pair,
                    platform=Platform.GENERIC_ASCII,
                    time_frame=TimeFrame.ONE_MINUTE,
                )
            finally:
                os.chdir(old)

        return _dl
    except (ImportError, AttributeError):
        pass

    # Tentative 2 : API histdata <= 0.1.3 (histdata.download)
    try:
        import histdata                               # noqa: PLC0415
        from histdata.api import Platform, TimeFrame  # noqa: PLC0415

        def _dl(pair, year, tmp_dir):
            old = os.getcwd()
            os.chdir(tmp_dir)
            try:
                histdata.download(
                    pair=pair,
                    year=str(year),
                    platform=Platform.GENERIC_ASCII,
                    time_frame=TimeFrame.ONE_MINUTE,
                )
            finally:
                os.chdir(old)

        return _dl
    except (ImportError, AttributeError):
        pass

    return None


DOWNLOAD_FN = _get_download_fn()


# ── Formats Histdata pris en charge ──────────────────────────────────────────
# Generic ASCII : YYYYMMDD HHMMSS,O,H,L,C,V  (virgule)
# MetaTrader    : YYYY.MM.DD HH:MM;O;H;L;C;V  (point-virgule)
def _parse_csv(f) -> pd.DataFrame:
    raw = f.read().decode("utf-8", errors="ignore")
    sep = ";" if ";" in raw[:300] else ","
    from io import StringIO
    df = pd.read_csv(StringIO(raw), sep=sep, header=None)

    if df.shape[1] == 6:
        df.columns = ["datetime", "Open", "High", "Low", "Close", "Volume"]
        # Essai format YYYYMMDD HHMMSS
        try:
            df["datetime"] = pd.to_datetime(df["datetime"], format="%Y%m%d %H%M%S")
        except Exception:
            df["datetime"] = pd.to_datetime(df["datetime"], infer_datetime_format=True)
    elif df.shape[1] == 7:
        df.columns = ["date", "time", "Open", "High", "Low", "Close", "Volume"]
        df["datetime"] = pd.to_datetime(
            df["date"].astype(str) + " " + df["time"].astype(str),
            infer_datetime_format=True,
        )
    else:
        raise ValueError(f"Format inconnu : {df.shape[1]} colonnes")

    df = df.set_index("datetime")[["Open", "High", "Low", "Close", "Volume"]]
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df


def download_year(pair: str, year: int, tmp_dir: Path) -> pd.DataFrame | None:
    if DOWNLOAD_FN is None:
        print("✗ Package 'histdata' introuvable. Installe-le : pip install histdata")
        return None

    print(f"  {year}...", end=" ", flush=True)
    try:
        DOWNLOAD_FN(pair, year, tmp_dir)

        # Cherche ZIP ou CSV produit
        zips  = list(tmp_dir.glob("*.zip"))
        csvs  = list(tmp_dir.glob("*.csv"))

        if zips:
            with zipfile.ZipFile(zips[0]) as zf:
                csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
                if not csv_names:
                    print("✗ Pas de CSV dans le ZIP")
                    return None
                with zf.open(csv_names[0]) as f:
                    df = _parse_csv(f)
            for z in zips:
                z.unlink()
        elif csvs:
            with open(csvs[0], "rb") as f:
                df = _parse_csv(f)
            for c in csvs:
                c.unlink()
        else:
            print("✗ Aucun fichier téléchargé")
            return None

        print(f"✓ {len(df):,} barres M1")
        return df

    except Exception as e:
        print(f"✗ {e}")
        return None


def resample_to_h1(m1: pd.DataFrame) -> pd.DataFrame:
    h1 = m1.resample("1h").agg({
        "Open":   "first",
        "High":   "max",
        "Low":    "min",
        "Close":  "last",
        "Volume": "sum",
    }).dropna(subset=["Open"])
    return h1[h1.index.dayofweek < 5]


def save_histdata_csv(df: pd.DataFrame, path: Path) -> None:
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
    parser = argparse.ArgumentParser(description="Download EURUSD Histdata.com → H1 CSV")
    parser.add_argument("--pair",   default="eurusd")
    parser.add_argument("--start",  type=int, default=2019)
    parser.add_argument("--end",    type=int, default=2024)
    parser.add_argument("--output", default="sample_data/EURUSD_H1_real.csv")
    args = parser.parse_args()

    if DOWNLOAD_FN is None:
        print("Erreur : package histdata non disponible.")
        print("  pip install histdata")
        sys.exit(1)

    years = list(range(args.start, args.end + 1))
    print(f"Téléchargement {args.pair.upper()} H1 — {years[0]} → {years[-1]}")
    print(f"Sortie : {args.output}\n")

    all_frames: list[pd.DataFrame] = []
    with tempfile.TemporaryDirectory() as tmp:
        for year in years:
            df_m1 = download_year(args.pair, year, Path(tmp))
            if df_m1 is not None:
                df_h1 = resample_to_h1(df_m1)
                all_frames.append(df_h1)
                print(f"         → {len(df_h1):,} barres H1")

    if not all_frames:
        print("\nAucune donnée téléchargée. Voir ci-dessus pour les erreurs.")
        sys.exit(1)

    merged = pd.concat(all_frames).sort_index()
    merged = merged[~merged.index.duplicated(keep="first")]
    out = Path(args.output)
    save_histdata_csv(merged, out)

    size_mb  = out.stat().st_size / 1_048_576
    atr_pips = (merged["High"] - merged["Low"]).mean() / 0.0001
    print(f"\n{'='*52}")
    print(f"  Fichier   : {out}")
    print(f"  Barres H1 : {len(merged):,}")
    print(f"  Période   : {merged.index[0].date()} → {merged.index[-1].date()}")
    print(f"  ATR moyen : {atr_pips:.1f} pips")
    print(f"  Taille    : {size_mb:.1f} MB")
    print(f"{'='*52}")
    print(f"\nLancer le backtest :")
    print(f"  python run_backtest.py --source histdata_csv \\")
    print(f"      --csv {out} \\")
    print(f"      --start {args.start}-01-01 --end {args.end + 1}-01-01 --equity 10000")


if __name__ == "__main__":
    main()
