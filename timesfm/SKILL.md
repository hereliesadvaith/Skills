---
name: timesfm
description: Use this skill whenever the user wants to forecast a time series or predict future values from historical numbers — sales, demand, stock levels, revenue, cash flow, website traffic, sensor readings, KPIs — or mentions TimesFM, Google's time-series foundation model. Zero-shot (no training): give it past values, get point forecasts plus 10–90% quantiles. Works on CSV/JSON files, inline numbers, or data pulled from Odoo via the odoo-acl skill.
---

# TimesFM forecasting

Run Google's TimesFM (pretrained time-series foundation model) locally. No training or fitting —
pass the history, get a forecast with uncertainty bands.

**Always run the scripts with the skill's own interpreter**, not the system `python3`:

```
PY=~/.claude/skills/timesfm/.venv/bin/python
```

## Setup (once)

```bash
bash ~/.claude/skills/timesfm/setup.sh
```

Creates `.venv` next to this file and installs CPU PyTorch + `timesfm[torch]` (≈ 1 GB). Use
`TIMESFM_CUDA=1 bash setup.sh` for a CUDA build. If `$PY` does not exist, run setup first.

Settings live in `.env` next to this file:

```
TIMESFM_MODEL=2.5          # 2.5 (default) or 3.0 — see "Model choice"
TIMESFM_DEVICE=cpu         # or cuda
TIMESFM_MAX_CONTEXT=1024   # how many trailing history points the model sees
HF_TOKEN=                  # only if a checkpoint is gated
```

## Test

```bash
$PY ~/.claude/skills/timesfm/forecast.py
```

Downloads the checkpoint on first run (2.5 ≈ 0.8 GB into `~/.cache/huggingface`), then forecasts a
toy series and prints `OK`. Whole run including model load is ~5–10 s on CPU; the forecast itself is sub-second.

## Forecast

```bash
# CSV: forecast every numeric column 12 steps ahead (date column auto-detected)
$PY ~/.claude/skills/timesfm/forecast.py sales.csv --horizon 12

# Pick columns, name the date column, save CSV
$PY ~/.claude/skills/timesfm/forecast.py sales.csv -H 6 --column qty,revenue --date-column date --out fc.csv

# JSON file or stdin: [1,2,3,...]  or  {"dates":[...], "qty":[...], "revenue":[...]}
$PY ~/.claude/skills/timesfm/forecast.py series.json -H 24
cat series.json | $PY ~/.claude/skills/timesfm/forecast.py - -H 24

# Inline numbers
$PY ~/.claude/skills/timesfm/forecast.py --values 120,132,129,141,150,148 -H 3

# Override the model for one run
$PY ~/.claude/skills/timesfm/forecast.py sales.csv -H 12 --model 3.0 --multivariate
```

Flags: `--horizon/-H` (default 12), `--column/-c`, `--date-column`, `--model 2.5|3.0`,
`--device cpu|cuda`, `--multivariate` (3.0 only: forecast all columns jointly), `--out file.csv|.json`,
`--compact`.

### Input rules
- One row per time step, **evenly spaced and in chronological order** (daily, weekly, monthly…).
  Aggregate first if the raw data is per-transaction.
- Missing values: leave blank / `null` / `NaN`. Leading gaps are dropped, interior gaps interpolated.
- Dates are optional. If present and regular (constant delta, or 1st-of-month monthly) the output
  includes `future_dates`; otherwise only `step` numbers.
- Give at least ~2–3 seasonal cycles of history for seasonal data (e.g. 24–36 months for yearly
  seasonality). Only the last `TIMESFM_MAX_CONTEXT` points are used.

### Output (stdout, JSON)

```json
{"model": "timesfm-2.5", "horizon": 12,
 "series": {"qty": {"context_length": 36, "future_dates": ["2026-01-01", ...],
                    "point": [...], "quantiles": {"q10": [...], "q20": [...], ..., "q90": [...]}}}}
```

`point` is the median forecast. `q10`…`q90` are the 10th–90th percentiles: use q10/q90 as an 80%
interval, q20/q80 as 60%. `--out x.csv` writes long format:
`series,step,date,point,q10,...,q90`.

When reporting to the user, give the point forecast **and** the q10–q90 band; never present the
point alone as certain. Round to the data's precision.

## From Odoo (with the odoo-acl skill)

Aggregate in Odoo with `read_group`, reshape to `{"dates": [...], "<name>": [...]}`, pipe in:

```bash
python ~/.claude/skills/odoo-acl/connect.py sale.report read_group \
  '[[["state","in",["sale","done"]]], ["product_uom_qty:sum"], ["date:month"]]' '{"lazy": false}' \
| python3 -c '
import json,sys; rows=json.load(sys.stdin)
print(json.dumps({"dates":[r["__range"]["date:month"]["from"][:10] for r in rows],
                  "qty":[r["product_uom_qty"] for r in rows]}))' \
| $PY ~/.claude/skills/timesfm/forecast.py - -H 6
```

Swap model/field as needed (`stock.move`, `account.move.line` with `balance:sum`, `date:week`, …).
Months with no sales are **absent** from `read_group` output, not zero — fill them in (insert 0 or
`null`) before forecasting so the spacing stays even. Filter by `product_id` / `team_id` / company
in the domain for per-item forecasts; forecast several items in one call by putting each in its own
JSON key.

## From Python

```python
import sys, os; sys.path.insert(0, os.path.expanduser("~/.claude/skills/timesfm"))
from forecast import load_model, forecast_series
model = load_model()                                  # once per process (slow)
res = forecast_series(model, {"qty": history, "rev": history2}, horizon=12, dates=dates)
res["series"]["qty"]["point"], res["series"]["qty"]["quantiles"]["q90"]
```

Must be run with `$PY`. For covariates (holidays, prices, promos) use the library directly:
2.5 → `model.backend.forecast_with_covariates(...)`; 3.0 → `model.backend.predict_batch(contexts,
horizon, past_only_covariates=[...], past_future_covariates=[...])` where each covariate is a
`(num_covariates, len)` float32 array and past+future ones have length `context + horizon`.

## Model choice

| | 2.5 (`google/timesfm-2.5-200m-pytorch`) | 3.0 (`google/timesfm-3.0-pytorch`) |
|---|---|---|
| Weights license | **Apache-2.0** — fine for client / production work | **Non-commercial, non-production only** |
| Size | 200M params, ~0.8 GB, fast on CPU | 300M params, ~1.2 GB |
| Inputs | univariate (batch of independent series) | native multivariate + covariates |
| Horizon | up to 1024 with quantiles | up to ~1k with quantiles |

Default is 2.5. Only switch to 3.0 for experiments/benchmarks, and tell the user about the license
when you do. Both come from the same `timesfm` PyPI package (v3.0.2), so no reinstall is needed.

## Notes
- First call in a process pays model load (~5 s CPU). Batch many series into one call rather
  than looping the CLI.
- Horizons longer than the history length or > ~⅓ of context get wide, less reliable bands — say so.
- The model is zero-shot and knows nothing about calendars, promotions or stock-outs unless you pass
  covariates; flag known one-off events in the history when interpreting results.
- CPU-only machine with ~4 GB free RAM is enough for 2.5. `TIMESFM_DEVICE=cpu` hides CUDA from torch.
- Never commit `.venv/` or the Hugging Face cache; `.env` holds no secrets unless `HF_TOKEN` is set.
