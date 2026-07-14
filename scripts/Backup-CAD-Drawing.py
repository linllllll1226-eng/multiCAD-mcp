"""CLI shim for the non-core CAD backup helper."""

# ruff: noqa: E402, I001

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cad_ux.backup import main  # noqa: E402


if __name__ == "__main__":
    main()
