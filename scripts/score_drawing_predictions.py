"""Score local prediction files against independently labelled source drawings."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cad_vision.corpus import score_predictions  # noqa: E402


def main() -> int:
    """Preserve metrics locally and fail unbound or false-complete submissions."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--predictions", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--require-recognition-complete", action="store_true")
    args = parser.parse_args()
    report = score_predictions(args.manifest, args.predictions)
    # Exclusive creation prevents replacing old evidence or input files.
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    print(
        f"Scored {report['case_count']} cases; false passes: {report['false_pass_count']}; "
        "live DWG: not evaluated"
    )
    return int(
        not report["binding_valid"]
        or report["false_pass_count"] > 0
        or (args.require_recognition_complete and not report["recognition_complete"])
    )


if __name__ == "__main__":
    raise SystemExit(main())
