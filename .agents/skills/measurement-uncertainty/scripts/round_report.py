"""Format a JSON mapping of labeled estimate/uncertainty pairs for display."""
import argparse
from decimal import Decimal, DecimalException
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from labcore.io import write_json
from labcore.reporting import rounded_standard


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.input.resolve() == args.out.resolve():
        parser.error("use a separate output file for display strings")
    try:
        pairs = json.loads(args.input.read_text(encoding="utf-8-sig"), parse_float=Decimal)
        if not isinstance(pairs, dict) or not pairs:
            raise ValueError("input must map labels to {value, u, digits?, recorded_value?}")
        output = {}
        for label, pair in pairs.items():
            value, u = rounded_standard(**pair)
            output[label] = {"value": value, "u": u}
        write_json(args.out, output)
    except (OSError, ValueError, TypeError, DecimalException) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
