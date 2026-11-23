"""Explicit model registry, independent first-order and sampling propagation.

This is a bounded numerical draft, not a general physics-model validator.
"""
import math
import importlib.metadata
import numpy as np
from scipy import stats


# Variable order, required SI units, output unit. Formula and gradient are explicit.
MODELS = {
    "identity": (("x",), ("1",), "1"),
    "sum": (("x", "z"), ("1", "1"), "1"),
    "difference": (("x", "z"), ("1", "1"), "1"),
    "product": (("x", "z"), ("1", "1"), "1"),
    "ratio": (("x", "z"), ("1", "1"), "1"),
    "pendulum_g": (("L", "T"), ("m", "s"), "m/s^2"),
    "cylinder_density": (("m", "D", "H"), ("kg", "m", "m"), "kg/m^3"),
}


def model_value(model_id, x):
    if model_id in {"pendulum_g", "cylinder_density"} and np.any(np.asarray(x) <= 0):
        raise ValueError("model requires positive physical inputs; revise distribution/domain")
    if model_id == "identity":
        return x[0]
    if model_id == "sum":
        return x[0] + x[1]
    if model_id == "difference":
        return x[0] - x[1]
    if model_id == "product":
        return x[0] * x[1]
    if model_id == "ratio":
        if np.any(np.asarray(x[1]) == 0):
            raise ValueError("zero denominator; no samples have been discarded")
        return x[0] / x[1]
    if model_id == "pendulum_g":
        return 4 * np.pi**2 * x[0] / x[1]**2
    if model_id == "cylinder_density":
        return 4 * x[0] / (np.pi * x[1]**2 * x[2])
    raise ValueError(f"unsupported model: {model_id}")


def gradient(model_id, x):
    if model_id == "identity":
        return np.array([1.0])
    if model_id == "sum":
        return np.array([1.0, 1.0])
    if model_id == "difference":
        return np.array([1.0, -1.0])
    if model_id == "product":
        return np.array([x[1], x[0]])
    if model_id == "ratio":
        return np.array([1/x[1], -x[0]/x[1]**2])
    y = model_value(model_id, x)
    if model_id == "pendulum_g":
        return np.array([y/x[0], -2*y/x[1]])
    if model_id == "cylinder_density":
        return np.array([y/x[0], -2*y/x[1], -y/x[2]])
    raise ValueError(f"unsupported gradient: {model_id}")


def assess_component(spec):
    kind = spec["kind"]
    if kind == "repeat_mean":
        x = np.asarray(spec["values"], dtype=float)
        if spec.get("independent") is not True:
            raise ValueError("s/sqrt(n) requires a declared independent-repeat model")
        if x.ndim != 1 or len(x) < 2 or not np.isfinite(x).all():
            raise ValueError("at least two finite independent repeats are required")
        s = float(np.std(x, ddof=1))
        return {"kind": kind, "value": float(x.mean()), "sample_std": s,
                "u": s/math.sqrt(len(x)), "dof": len(x)-1, "type": "A"}
    if kind == "resolution":
        val = float(spec["d"])
        u = val/math.sqrt(12)
    elif kind == "uniform_limit":
        val = float(spec["half_width"])
        u = val/math.sqrt(3)
    elif kind == "expanded":
        val, k = float(spec["U"]), float(spec["k"])
        if not math.isfinite(k) or k <= 0:
            raise ValueError("k must be positive and finite")
        u = val/k
    elif kind == "standard":
        val = u = float(spec["u"])
    else:
        raise ValueError(f"unsupported component kind: {kind}")
    if not math.isfinite(val) or val < 0:
        raise ValueError("uncertainty bounds must be finite and nonnegative")
    return {"kind": kind, "u": u, "type": "B" if kind != "standard" else "declared",
            "note": "Single-source conversion; distribution/source/overlap require assessment."}


def validate_spec(spec):
    model_id = spec["model_id"]
    if model_id not in MODELS:
        raise ValueError(f"unsupported model: {model_id}")
    names, units, output_unit = MODELS[model_id]
    if set(spec["inputs"]) != set(names):
        raise ValueError(f"model inputs must be exactly {names}")
    variables = [spec["inputs"][n] for n in names]
    for name, unit, v in zip(names, units, variables):
        if v.get("unit") != unit or not v.get("source"):
            raise ValueError(f"{name}: expected unit {unit} and a declared source")
        if v.get("distribution") not in {"normal", "uniform", "t"}:
            raise ValueError(f"{name}: explicitly declare normal/uniform/t distribution")
        if v["distribution"] == "t" and (not math.isfinite(float(v.get("df", 0))) or v["df"] <= 2):
            raise ValueError("t input needs df>2; u is a standard deviation, not scale")
    values = np.array([v["value"] for v in variables], dtype=float)
    us = np.array([v["u"] for v in variables], dtype=float)
    if not np.isfinite(values).all() or not np.isfinite(us).all() or np.any(us < 0):
        raise ValueError("input values/u must be finite, with nonnegative u")
    if type(spec.get("independent")) is not bool:
        raise ValueError("explicit independent=true/false is required")
    n = len(names)
    if not spec["independent"] and "correlation_matrix" not in spec:
        raise ValueError("correlated inputs require correlation_matrix in model variable order")
    corr = np.asarray(spec.get("correlation_matrix", np.eye(n)), dtype=float)
    if corr.shape != (n, n) or not np.isfinite(corr).all() or not np.allclose(corr, corr.T):
        raise ValueError("correlation matrix must be finite and symmetric")
    if not np.allclose(np.diag(corr), 1) or np.any(np.abs(corr) > 1+1e-12):
        raise ValueError("invalid correlation diagonal or coefficient")
    if np.linalg.eigvalsh(corr).min() < -1e-10:
        raise ValueError("correlation matrix is not positive semidefinite")
    if spec["independent"] and not np.allclose(corr, np.eye(n)):
        raise ValueError("independent=true contradicts correlation_matrix")
    if not spec["independent"] and any(v["distribution"] != "normal" for v in variables):
        raise ValueError("correlated nonnormal inputs not implemented; do not approximate silently")
    coverage = float(spec.get("coverage", .95))
    if not 0 < coverage < 1:
        raise ValueError("coverage must be between zero and one")
    if not spec.get("assumptions") or not isinstance(spec["assumptions"], list):
        raise ValueError("record model assumptions explicitly")
    model_value(model_id, values)
    return names, variables, values, us, corr, output_unit


def analytic(spec):
    names, variables, x, us, corr, unit = validate_spec(spec)
    covariance = np.outer(us, us)*corr
    c = gradient(spec["model_id"], x)
    variance = float(c @ covariance @ c)
    # Allow floating cancellation for exact common-mode cancellation.
    tolerance = 1e-12 * max(float(np.sum(np.abs(np.outer(c, c)*covariance))), 1e-300)
    if variance < -tolerance:
        raise ValueError("negative propagated variance")
    u = math.sqrt(max(variance, 0.0))
    budget = [{"input": name, "value": float(x[i]), "unit": v["unit"], "u": float(us[i]),
               "distribution": v["distribution"], "source": v["source"],
               "sensitivity": float(c[i]), "variance_contribution": float((c[i]*us[i])**2)}
              for i, (name, v) in enumerate(zip(names, variables))]
    pairs = [{"inputs": [names[i], names[j]], "variance_contribution": float(2*c[i]*c[j]*covariance[i, j])}
             for i in range(len(names)) for j in range(i+1, len(names)) if covariance[i, j] != 0]
    result = {"estimate": float(model_value(spec["model_id"], x)), "u": u, "unit": unit,
              "budget": budget, "covariance_terms": pairs, "method": "first_order"}
    if "coverage_factor" in spec:
        k = float(spec["coverage_factor"])
        if not math.isfinite(k) or k <= 0 or not spec.get("coverage_basis"):
            raise ValueError("coverage_factor requires positive finite k and coverage_basis")
        result["expanded"] = {"U": k*u, "k": k, "basis": spec["coverage_basis"]}
    return result


def monte_carlo(spec):
    names, variables, x, us, corr, unit = validate_spec(spec)
    cfg = spec.get("mc", {})
    backend = cfg.get("backend", "numpy-scipy")
    if backend not in {"numpy-scipy", "punpy"}:
        raise ValueError("MC backend must be numpy-scipy or punpy; no silent substitution")
    batches, size = cfg.get("batches", 5), cfg.get("samples_per_batch", 20000)
    if type(batches) is not int or type(size) is not int or not 3 <= batches <= 50 or not 1000 <= size <= 2000000:
        raise ValueError("MC needs 3..50 batches, 1000..2000000 samples/batch")
    seed = cfg.get("seed", 42)
    if type(seed) is not int or seed < 0:
        raise ValueError("seed must be a nonnegative integer")
    seeds = np.random.SeedSequence(seed).spawn(batches)
    eig, basis = np.linalg.eigh(corr)
    root = basis @ np.diag(np.sqrt(np.maximum(eig, 0)))
    ys, batch_us, native_us = [], [], []
    for child_seed in seeds:
        rng = np.random.default_rng(child_seed)
        if not spec["independent"]:
            sample = x[:, None] + us[:, None] * (root @ rng.standard_normal((len(names), size)))
        else:
            rows = []
            for v, value, u in zip(variables, x, us):
                if v["distribution"] == "normal":
                    row = stats.norm.rvs(loc=value, scale=u, size=size, random_state=rng) if u else np.full(size, value)
                elif v["distribution"] == "uniform":
                    row = stats.uniform.rvs(loc=value-math.sqrt(3)*u, scale=2*math.sqrt(3)*u, size=size, random_state=rng) if u else np.full(size, value)
                else:
                    df = v["df"]
                    row = stats.t.rvs(df, loc=value, scale=u*math.sqrt((df-2)/df), size=size, random_state=rng) if u else np.full(size, value)
                rows.append(row)
            sample = np.asarray(rows)
        if backend == "punpy":
            y, native_u = _punpy_propagate(spec["model_id"], x, us, corr, sample)
            native_us.append(native_u)
        else:
            with np.errstate(over="raise", invalid="raise", divide="raise"):
                y = np.asarray(model_value(spec["model_id"], sample), dtype=float)
        if y.shape != (size,) or not np.isfinite(y).all():
            raise ValueError("invalid model samples; no observations silently discarded")
        ys.append(y)
        batch_us.append(float(np.std(y, ddof=1)))
    y = np.concatenate(ys)
    u = float(np.std(y, ddof=1))
    spread = float(np.ptp(batch_us)/u) if u else 0.0
    threshold = float(cfg.get("stability_rtol", .03))
    if not math.isfinite(threshold) or threshold <= 0:
        raise ValueError("stability_rtol must be positive and finite")
    tail = (1-float(spec.get("coverage", .95)))/2
    result = {"mean": float(y.mean()), "u": u, "unit": unit,
            "backend": backend, "sampler": "numpy.random.Generator + scipy.stats",
            "versions": {p: importlib.metadata.version(p) for p in (["numpy", "scipy", "punpy"] if backend == "punpy" else ["numpy", "scipy"])},
            "std_ddof": 1,
            "interval": np.quantile(y, [tail, 1-tail]).tolist(), "interval_type": "equal_tail",
            "coverage": float(spec.get("coverage", .95)), "samples": int(len(y)), "seed": seed,
            "batch_u": batch_us, "batch_relative_range": spread, "stable": spread <= threshold,
            "stability_rtol": threshold, "invalid_samples": 0,
            "notes": ["Batch-u diagnostic does not certify coverage-endpoint precision."]}
    if native_us:
        result["punpy_native_u_by_batch"] = native_us
        result["notes"].append("Punpy receives the same explicit input samples; this is not an independent sampling implementation. Output u is standardized to sample std(ddof=1).")
    return result


def _punpy_propagate(model_id, x, us, corr, samples):
    try:
        import punpy
    except ImportError as exc:
        raise RuntimeError("Punpy backend unavailable; install requirements-mc.txt or explicitly select numpy-scipy") from exc
    size = samples.shape[1]

    def guarded(*values):
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            y = np.asarray(model_value(model_id, values), dtype=float)
        if not np.isfinite(y).all():
            raise ValueError("invalid model samples; no filtering is permitted")
        return y

    prop = punpy.MCPropagation(size, parallel_cores=0, dtype=np.float64)
    if np.any(us):
        native_u, y, _ = prop.propagate_standard(
            guarded, [np.asarray(v) for v in x], [np.asarray(u) for u in us],
            ["rand"]*len(x), corr_between=corr, samples=samples,
            return_samples=True, allow_some_nans=False)
    else:
        # Deterministic inputs do not need a distribution summary; still use Punpy's sample runner.
        y = prop.run_samples(guarded, samples, allow_some_nans=False)
        native_u = 0.0
    y = np.asarray(y, dtype=float)
    if y.shape != (size,) or not np.isfinite(y).all():
        raise ValueError("Punpy altered sample count or produced nonfinite output; run rejected")
    return y, float(native_u)


def propagate(spec):
    a, m = analytic(spec), monte_carlo(spec)
    gap = abs(a["u"] - m["u"])
    relative = gap/a["u"] if a["u"] else (0.0 if m["u"] == 0 else None)
    return {"model_id": spec["model_id"], "analytic": a, "mc": m,
            "comparison": {"absolute_u_difference": gap, "relative_u_difference": relative,
                           "status": "diagnostic_only",
                           "note": "Interpret using approximation validity, sampling stability and task-specific criteria; no universal pass threshold."}}
