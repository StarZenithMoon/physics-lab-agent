import csv
from collections import Counter
from pathlib import Path
import numpy as np


def profile_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames
        if not fields or len(fields) != len(set(fields)) or any(not f.strip() for f in fields):
            raise ValueError("CSV requires unique, nonempty column names")
        rows = list(reader)
    if not rows or any(None in row for row in rows):
        raise ValueError("empty CSV or extra cells without a column")
    duplicates = [list(k) for k, n in Counter(tuple(row.get(f) for f in fields) for row in rows).items() if n > 1]
    columns = {}
    for name in fields:
        values, indices, invalid = [], [], []
        for line, row in enumerate(rows, 2):
            raw = row.get(name)
            try:
                value = float(raw)
                if not np.isfinite(value):
                    raise ValueError()
            except (TypeError, ValueError):
                invalid.append({"row": line, "raw": raw})
                continue
            values.append(value)
            indices.append(line)
        x = np.asarray(values, dtype=float)
        summary = {"n": len(values), "invalid": invalid, "row_numbers": indices,
                   "values": values, "mean": None, "sample_std": None, "mad_flags": [], "sequence_slope": None}
        if len(x):
            summary["mean"] = float(x.mean())
            summary["min"], summary["max"] = float(x.min()), float(x.max())
            med = np.median(x)
            mad = np.median(np.abs(x - med))
            if mad > 0:
                summary["mad_flags"] = [indices[i] for i in np.flatnonzero(0.67448975 * np.abs(x - med) / mad > 3.5)]
        if len(x) > 1:
            summary["sample_std"] = float(x.std(ddof=1))
            summary["sequence_slope"] = float(np.polyfit(indices, x, 1)[0])
        columns[name] = summary
    return {"rows": len(rows), "duplicate_rows": duplicates, "columns": columns,
            "notes": ["No observations were deleted; statistics exclude only explicitly listed invalid cells.",
                      "Sequence slope and MAD flags are descriptive, not evidence for automatic removal."]}
