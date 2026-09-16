#!/usr/bin/env python3
"""Forecast time series with Google's TimesFM, using the settings in the skill's .env file.

Always run with the skill's own interpreter (created by setup.sh):
    PY=~/.claude/skills/timesfm/.venv/bin/python

Usage:
    $PY forecast.py                                        # smoke test: load model, forecast a toy series
    $PY forecast.py data.csv --horizon 12                  # forecast every numeric column of a CSV
    $PY forecast.py data.csv --horizon 12 --column qty --date-column date --out fc.csv
    $PY forecast.py series.json --horizon 6                # JSON: [1,2,3] or {"name": [..], "other": [..]}
    $PY forecast.py --values 3,5,4,6,7,8 --horizon 3       # inline numbers
    cat series.json | $PY forecast.py - --horizon 12       # stdin
    $PY forecast.py data.csv --horizon 12 --model 3.0 --multivariate   # 3.0 only: forecast columns jointly

Output is JSON on stdout: {"model", "horizon", "series": {name: {"context_length", "future_dates",
"point": [...], "quantiles": {"q10": [...], ..., "q90": [...]}}}}. --out writes CSV (long format)
or JSON depending on the extension. Progress/log lines go to stderr.

Importable helper:
    import sys; sys.path.insert(0, "~/.claude/skills/timesfm")   # expand ~ first
    from forecast import load_model, forecast_series
    model = load_model()                                   # honours .env; heavy, call once
    result = forecast_series(model, {"sales": [12, 15, 14, ...]}, horizon=12)
    result["series"]["sales"]["point"]
"""
import argparse
import csv
import json
import math
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
CHECKPOINTS = {
    "2.5": "google/timesfm-2.5-200m-pytorch",   # Apache-2.0 weights
    "3.0": "google/timesfm-3.0-pytorch",        # non-commercial license
}
QUANTILE_LEVELS = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
QUANTILE_KEYS = [f"q{int(q * 100)}" for q in QUANTILE_LEVELS]
DATE_COLUMN_HINTS = ("date", "ds", "timestamp", "time", "period", "month", "day", "week", "year")


def log(msg):
    print(msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- config
def load_env():
    """Load KEY=VALUE pairs from the .env next to this script; real env vars win."""
    env_path = SKILL_DIR / ".env"
    cfg = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            cfg[key.strip()] = val.strip()

    def get(key, default=""):
        return os.environ.get(key, cfg.get(key, default))

    return {
        "model": get("TIMESFM_MODEL", "2.5"),
        "device": get("TIMESFM_DEVICE", "cpu").lower(),
        "max_context": int(get("TIMESFM_MAX_CONTEXT", "1024") or 1024),
        "hf_token": get("HF_TOKEN") or None,
    }


def _require_timesfm():
    try:
        import timesfm  # noqa: F401
    except ImportError:
        raise SystemExit(
            f"timesfm is not importable from {sys.executable}.\n"
            f"Run:  bash {SKILL_DIR}/setup.sh\n"
            f"then call this script with {SKILL_DIR}/.venv/bin/python"
        )


# --------------------------------------------------------------------------- model wrapper
class Model:
    """Uniform wrapper over TimesFM 2.5 and 3.0. Use load_model() to build one."""

    def __init__(self, version, backend, cfg):
        self.version = version
        self.checkpoint = CHECKPOINTS[version]
        self.backend = backend
        self.cfg = cfg
        self._compiled_horizon = 0

    # -- 2.5 -------------------------------------------------------------
    def _compile_2p5(self, horizon):
        import timesfm
        max_horizon = max(128, math.ceil(horizon / 128) * 128)
        if max_horizon <= self._compiled_horizon:
            return
        max_context = max(32, math.ceil(self.cfg["max_context"] / 32) * 32)
        if max_context + max_horizon > 16384:
            raise SystemExit(f"max_context ({max_context}) + horizon cap ({max_horizon}) exceeds 16384.")
        self.backend.compile(
            timesfm.ForecastConfig(
                max_context=max_context,
                max_horizon=max_horizon,
                normalize_inputs=True,
                use_continuous_quantile_head=max_horizon <= 1024,
                force_flip_invariance=True,
                infer_is_positive=True,
                fix_quantile_crossing=True,
            )
        )
        self._compiled_horizon = max_horizon

    def _forecast_2p5(self, arrays, horizon):
        import numpy as np
        self._compile_2p5(horizon)
        point, quant = self.backend.forecast(horizon=horizon, inputs=[np.asarray(a, dtype=np.float32) for a in arrays])
        # quant: (N, H, 10) = [mean, q10, q20, ..., q90]
        return point[:, :horizon], quant[:, :horizon, 1:10]

    # -- 3.0 -------------------------------------------------------------
    def _forecast_3p0(self, arrays, horizon, multivariate=False):
        import numpy as np
        if multivariate:
            lengths = {len(a) for a in arrays}
            if len(lengths) != 1:
                raise SystemExit("--multivariate needs all series to have the same length.")
            contexts = [np.stack([np.asarray(a, dtype=np.float32) for a in arrays])]
        else:
            contexts = [np.asarray(a, dtype=np.float32) for a in arrays]
        outs = list(self.backend.predict_batch(contexts, horizon=horizon, return_quantiles=True,
                                               univariate=not multivariate))
        if multivariate:
            point = np.asarray(outs[0].forecast)          # (V, H)
            quant = np.asarray(outs[0].quantiles)         # (V, H, 9)
        else:
            point = np.stack([np.asarray(o.forecast) for o in outs])
            quant = np.stack([np.asarray(o.quantiles) for o in outs])
        return point[:, :horizon], quant[:, :horizon, :]

    def forecast(self, arrays, horizon, multivariate=False):
        """arrays: list of 1-D float sequences (NaN allowed). Returns (point (N,H), quantiles (N,H,9))."""
        if multivariate and self.version != "3.0":
            raise SystemExit("--multivariate requires TIMESFM_MODEL=3.0 (2.5 is univariate only).")
        if self.version == "2.5":
            return self._forecast_2p5(arrays, horizon)
        return self._forecast_3p0(arrays, horizon, multivariate)


def load_model(version=None, device=None):
    """Load TimesFM according to .env (or the overrides). Downloads the checkpoint on first use."""
    cfg = load_env()
    version = str(version or cfg["model"]).strip()
    if version not in CHECKPOINTS:
        raise SystemExit(f"TIMESFM_MODEL must be one of {list(CHECKPOINTS)}, got {version!r}")
    device = (device or cfg["device"]).lower()
    if device == "cpu":
        os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")   # 2.5 picks CUDA automatically otherwise
    if cfg["hf_token"]:
        os.environ.setdefault("HF_TOKEN", cfg["hf_token"])
    _require_timesfm()

    log(f"Loading TimesFM {version} ({CHECKPOINTS[version]}) on {device} ...")
    if version == "2.5":
        import timesfm
        backend = timesfm.TimesFM_2p5_200M_torch.from_pretrained(CHECKPOINTS[version], token=cfg["hf_token"])
    else:
        import timesfm3
        backend = timesfm3.TimesFM3Evaluator(
            timesfm3.ModelConfig(checkpoint_path=CHECKPOINTS[version], device=device, token=cfg["hf_token"])
        )
    return Model(version, backend, cfg)


# --------------------------------------------------------------------------- input parsing
def _to_float(s):
    if s is None:
        return math.nan
    s = str(s).strip().replace(",", "")
    if s == "" or s.lower() in ("nan", "null", "none", "na", "n/a"):
        return math.nan
    return float(s)


def _is_numeric_col(values):
    seen = False
    for v in values:
        try:
            f = _to_float(v)
        except ValueError:
            return False
        seen = seen or not math.isnan(f)
    return seen


def _parse_date(s):
    s = str(s).strip()
    for fmt in (None, "%Y-%m", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%B %Y", "%b %Y", "%Y"):
        try:
            return datetime.fromisoformat(s) if fmt is None else datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(s)


def _looks_like_dates(values):
    try:
        for v in values:
            _parse_date(v)
        return True
    except ValueError:
        return False


def read_csv(path_or_fh, column=None, date_column=None):
    rows = list(csv.DictReader(path_or_fh))
    if not rows:
        raise SystemExit("CSV has no data rows.")
    headers = list(rows[0].keys())
    columns = {h: [r.get(h) for r in rows] for h in headers}

    if date_column is None:
        for h in headers:
            if h.lower() in DATE_COLUMN_HINTS or (not _is_numeric_col(columns[h]) and _looks_like_dates(columns[h])):
                date_column = h
                break
    dates = [str(v).strip() for v in columns[date_column]] if date_column else None

    if column:
        wanted = [c.strip() for c in column.split(",")]
        missing = [c for c in wanted if c not in columns]
        if missing:
            raise SystemExit(f"Column(s) not found: {missing}. Available: {headers}")
    else:
        wanted = [h for h in headers if h != date_column and _is_numeric_col(columns[h])]
        if not wanted:
            raise SystemExit(f"No numeric columns found in {headers}")
    series = {c: [_to_float(v) for v in columns[c]] for c in wanted}
    return series, dates


def read_json(text):
    data = json.loads(text)
    dates = None
    if isinstance(data, list):
        return {"series": [_to_float(v) for v in data]}, None
    if isinstance(data, dict):
        series = {}
        for k, v in data.items():
            if isinstance(v, list) and v and all(isinstance(x, str) for x in v) and k.lower() in DATE_COLUMN_HINTS + ("dates",):
                dates = v
            elif isinstance(v, list):
                series[k] = [_to_float(x) for x in v]
        if not series:
            raise SystemExit("JSON must be a list of numbers or an object of name -> list of numbers.")
        return series, dates
    raise SystemExit("Unsupported JSON shape.")


def read_input(source, values=None, column=None, date_column=None):
    """Returns (series: {name: [floats]}, dates: [str] | None)."""
    if values:
        return {"series": [_to_float(v) for v in values.split(",")]}, None
    if source is None:
        raise SystemExit("Give an input file, '-' for stdin, or --values.")
    if source == "-":
        text = sys.stdin.read()
        stripped = text.lstrip()
        if stripped.startswith("[") or stripped.startswith("{"):
            return read_json(text)
        import io
        return read_csv(io.StringIO(text), column, date_column)
    p = Path(source).expanduser()
    if not p.exists():
        raise SystemExit(f"Input not found: {p}")
    if p.suffix.lower() == ".json":
        return read_json(p.read_text())
    with p.open(newline="") as fh:
        return read_csv(fh, column, date_column)


def clean_series(vals):
    """Drop leading NaNs, linearly interpolate interior NaNs, hold last value for trailing NaNs."""
    i = 0
    while i < len(vals) and math.isnan(vals[i]):
        i += 1
    vals = vals[i:]
    if not vals:
        raise SystemExit("Series is empty / all NaN.")
    out = list(vals)
    n = len(out)
    j = 0
    while j < n:
        if math.isnan(out[j]):
            k = j
            while k < n and math.isnan(out[k]):
                k += 1
            left = out[j - 1]
            right = out[k] if k < n else left
            for t in range(j, k):
                out[t] = left + (right - left) * (t - j + 1) / (k - j + 1)
            j = k
        else:
            j += 1
    return out


# --------------------------------------------------------------------------- dates
def _add_months(d, n):
    y, m = divmod(d.month - 1 + n, 12)
    y += d.year
    m += 1
    last = [31, 29 if y % 4 == 0 and (y % 100 != 0 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return d.replace(year=y, month=m, day=min(d.day, last))


def future_dates(dates, horizon):
    """Extend a regularly spaced date column by `horizon` steps. Returns None if the spacing is irregular."""
    if not dates or len(dates) < 2:
        return None
    try:
        parsed = [_parse_date(d) for d in dates]
    except ValueError:
        return None
    has_time = any(d.time() != datetime.min.time() for d in parsed)
    fmt = (lambda d: d.isoformat(sep=" ")) if has_time else (lambda d: d.date().isoformat())
    last = parsed[-1]

    deltas = {parsed[i + 1] - parsed[i] for i in range(len(parsed) - 1)}
    if len(deltas) == 1:
        step = deltas.pop()
        if step.total_seconds() <= 0:
            return None
        return [fmt(last + step * (h + 1)) for h in range(horizon)]

    month_steps = {(b.year - a.year) * 12 + (b.month - a.month) for a, b in zip(parsed, parsed[1:])}
    if len(month_steps) == 1 and len({d.day for d in parsed}) == 1:
        n = month_steps.pop()
        if n > 0:
            return [fmt(_add_months(last, n * (h + 1))) for h in range(horizon)]
    return None


# --------------------------------------------------------------------------- main API
def forecast_series(model, series, horizon, dates=None, multivariate=False):
    """series: {name: [floats]}. Returns the result dict described in the module docstring."""
    names = list(series)
    arrays = [clean_series(list(series[n])) for n in names]
    if horizon < 1:
        raise SystemExit("--horizon must be >= 1")
    log(f"Forecasting {len(names)} series, horizon={horizon} ...")
    point, quant = model.forecast(arrays, horizon, multivariate=multivariate)
    fdates = future_dates(dates, horizon) if dates else None
    out = {"model": f"timesfm-{model.version}", "checkpoint": model.checkpoint, "horizon": horizon, "series": {}}
    for i, name in enumerate(names):
        out["series"][name] = {
            "context_length": len(arrays[i]),
            "future_dates": fdates,
            "point": [round(float(v), 6) for v in point[i]],
            "quantiles": {key: [round(float(v), 6) for v in quant[i, :, q]] for q, key in enumerate(QUANTILE_KEYS)},
        }
    return out


def write_out(result, path):
    p = Path(path).expanduser()
    if p.suffix.lower() == ".json":
        p.write_text(json.dumps(result, indent=2))
    else:
        with p.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["series", "step", "date", "point"] + QUANTILE_KEYS)
            for name, s in result["series"].items():
                for h in range(result["horizon"]):
                    d = s["future_dates"][h] if s["future_dates"] else ""
                    w.writerow([name, h + 1, d, s["point"][h]] + [s["quantiles"][k][h] for k in QUANTILE_KEYS])
    log(f"Wrote {p}")


def smoke_test(model):
    import numpy as np
    t = np.arange(120)
    toy = 100 + 0.5 * t + 10 * np.sin(2 * np.pi * t / 12)
    res = forecast_series(model, {"toy": toy.tolist()}, horizon=12)
    import torch
    print(f"TimesFM {model.version} loaded from {model.checkpoint}")
    print(f"torch {torch.__version__}, cuda available: {torch.cuda.is_available()}, python {sys.version.split()[0]}")
    print("Toy forecast (next 12):", [round(v, 1) for v in res["series"]["toy"]["point"]])
    print("OK")


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", nargs="?", help="CSV/JSON file, or '-' for stdin")
    ap.add_argument("--values", help="inline comma-separated numbers")
    ap.add_argument("--horizon", "-H", type=int, default=12, help="steps to forecast (default 12)")
    ap.add_argument("--column", "-c", help="CSV column(s) to forecast, comma-separated (default: all numeric)")
    ap.add_argument("--date-column", help="CSV date column (default: auto-detect)")
    ap.add_argument("--model", choices=list(CHECKPOINTS), help="override TIMESFM_MODEL from .env")
    ap.add_argument("--device", choices=["cpu", "cuda"], help="override TIMESFM_DEVICE from .env")
    ap.add_argument("--multivariate", action="store_true", help="3.0 only: forecast all columns jointly")
    ap.add_argument("--out", "-o", help="write result to .csv (long format) or .json")
    ap.add_argument("--compact", action="store_true", help="print compact JSON instead of indented")
    args = ap.parse_args(argv)

    model = load_model(args.model, args.device)
    if args.input is None and not args.values:
        smoke_test(model)
        return

    series, dates = read_input(args.input, args.values, args.column, args.date_column)
    result = forecast_series(model, series, args.horizon, dates, multivariate=args.multivariate)
    if args.out:
        write_out(result, args.out)
    print(json.dumps(result, separators=(",", ":")) if args.compact else json.dumps(result, indent=2))


if __name__ == "__main__":
    main(sys.argv[1:])
