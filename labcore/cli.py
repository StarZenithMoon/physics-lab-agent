import argparse
import json
from .io import read_json, write_json
from .data import profile_csv
from .fit import fit_model
from .uncertainty import assess_component, propagate


def main(action=None):
    parser = argparse.ArgumentParser()
    if action is None:
        parser.add_argument("action", choices=["profile", "fit", "assess", "propagate"])
    parser.add_argument("input")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    action = action or args.action
    functions = {"profile": profile_csv, "fit": fit_model, "assess": assess_component, "propagate": propagate}
    result = functions[action](args.input if action == "profile" else read_json(args.input))
    write_json(args.out, result)
    print(json.dumps({"action": action, "output": args.out, "status": "written"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
