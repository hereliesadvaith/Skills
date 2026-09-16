#!/usr/bin/env bash
# One-time setup: create the skill's own virtualenv and install TimesFM (CPU torch by default).
#   bash ~/.claude/skills/timesfm/setup.sh          # CPU build of torch (small, no GPU needed)
#   TIMESFM_CUDA=1 bash ~/.claude/skills/timesfm/setup.sh   # default PyPI torch with CUDA
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-$(command -v /usr/bin/python3 || command -v python3)}"

if [ ! -x "$DIR/.venv/bin/python" ]; then
  echo "Creating venv at $DIR/.venv with $PY"
  "$PY" -m venv "$DIR/.venv"
fi
PIP="$DIR/.venv/bin/pip"
"$PIP" install -q -U pip

if [ "${TIMESFM_CUDA:-0}" = "1" ]; then
  "$PIP" install -q torch
else
  "$PIP" install -q torch --index-url https://download.pytorch.org/whl/cpu
fi
"$PIP" install -q "timesfm[torch]"

"$DIR/.venv/bin/python" - <<'PYEOF'
import torch, timesfm, timesfm3
from importlib.metadata import version
print(f"torch {torch.__version__} (cuda available: {torch.cuda.is_available()})")
print(f"timesfm {version('timesfm')} — exports: TimesFM_2p5_200M_torch={hasattr(timesfm,'TimesFM_2p5_200M_torch')}, TimesFM3Evaluator={hasattr(timesfm3,'TimesFM3Evaluator')}")
PYEOF
echo "Setup done. Run: $DIR/.venv/bin/python $DIR/forecast.py"
