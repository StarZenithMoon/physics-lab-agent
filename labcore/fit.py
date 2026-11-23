"""Uniform, explicit adapters for SciPy, lmfit and odrpack."""
import importlib.metadata
import warnings
import numpy as np
from scipy.linalg import solve_triangular
from scipy.optimize import curve_fit
from .expressions import expression, valid_parameter

MODELS = {
    "linear": ("intercept+slope*x", ("intercept", "slope")),
    "quadratic": ("intercept+linear*x+quadratic*x**2", ("intercept", "linear", "quadratic")),
    "exponential_decay": ("offset+amplitude*exp(-x/tau)", ("offset", "amplitude", "tau")),
    "gaussian": ("offset+amplitude*exp(-0.5*((x-center)/sigma)**2)", ("offset", "amplitude", "center", "sigma")),
}


def _finite_vector(value, shape, label, nonnegative=False):
    result = np.asarray(value, dtype=float)
    if result.shape != shape or not np.isfinite(result).all() or (nonnegative and np.any(result < 0)):
        raise ValueError(f"{label} has invalid shape, nonfinite or negative values")
    return result


def _setup(spec):
    x, y = np.asarray(spec["x"], dtype=float), np.asarray(spec["y"], dtype=float)
    if x.ndim != 1 or len(x) < 3 or y.shape != x.shape or not np.isfinite([x, y]).all():
        raise ValueError("need at least three finite paired x/y observations")
    if not spec.get("x_unit") or not spec.get("y_unit"):
        raise ValueError("declare x_unit and y_unit")
    if "cov_x" in spec or "cov_xy" in spec:
        raise ValueError("cross-observation x/cross-axis covariance is not supported by this adapter")
    ux = _finite_vector(spec.get("ux", np.zeros_like(x)), x.shape, "ux", True)
    if "uy" in spec and "cov_y" in spec:
        raise ValueError("supply either uy or cov_y, not both")
    covariance_y = None
    if "cov_y" in spec:
        covariance_y = _finite_vector(spec["cov_y"], (len(x), len(x)), "cov_y")
        if not np.allclose(covariance_y, covariance_y.T):
            raise ValueError("cov_y must be symmetric")
        try:
            cholesky = np.linalg.cholesky(covariance_y)
        except np.linalg.LinAlgError as exc:
            raise ValueError("cov_y must be positive definite") from exc
        uy = np.sqrt(np.diag(covariance_y))
    else:
        uy = _finite_vector(spec.get("uy", np.ones_like(x)), x.shape, "uy")
        if np.any(uy <= 0):
            raise ValueError("uy must be strictly positive standard uncertainties")
        cholesky = np.diag(uy)
    weighted = "uy" in spec or "cov_y" in spec
    absolute = spec.get("absolute_sigma", weighted)
    if type(absolute) is not bool or (absolute and not weighted):
        raise ValueError("absolute_sigma must be Boolean; true requires supplied uy/cov_y")
    model_id = spec.get("model", "linear")
    if model_id == "expression":
        if not isinstance(spec.get("parameters"), dict) or not spec["parameters"]:
            raise ValueError("expression models require named parameters with initial values")
        names, source = tuple(spec["parameters"]), spec.get("expression")
    elif model_id in MODELS:
        source, names = MODELS[model_id]
    else:
        raise ValueError(f"unknown fit model: {model_id}; use model=expression for custom arithmetic models")
    if not all(valid_parameter(n) for n in names):
        raise ValueError("invalid or reserved parameter name")
    f = expression(source, ["x", *names])
    supplied = spec.get("parameters")
    if supplied is None and model_id == "linear":
        if np.ptp(x) == 0:
            raise ValueError("rank-deficient linear fit: all x are identical")
        slope, intercept = np.polyfit(x, y, 1)
        supplied = {"intercept": float(intercept), "slope": float(slope)}
    if not isinstance(supplied, dict) or set(supplied) != set(names):
        raise ValueError(f"supply initial parameters exactly {names}")
    params = {}
    for name in names:
        p = supplied[name]
        p = dict(p) if isinstance(p, dict) else {"value": p}
        if set(p)-{"value", "min", "max", "vary", "expr"}:
            raise ValueError(f"unknown parameter options for {name}")
        value = float(p["value"])
        lower = -np.inf if p.get("min") is None else float(p["min"])
        upper = np.inf if p.get("max") is None else float(p["max"])
        vary = p.get("vary", True)
        if not np.isfinite(value) or np.isnan([lower, upper]).any() or lower >= upper or not lower <= value <= upper or type(vary) is not bool:
            raise ValueError(f"invalid value, bounds or vary for {name}")
        if p.get("expr"):
            expression(p["expr"], names)
        params[name] = {"value": value, "min": lower, "max": upper, "vary": vary, "expr": p.get("expr")}
    free = [name for name, p in params.items() if p["vary"] and not p["expr"]]
    if not free or len(x) <= len(free):
        raise ValueError("require positive residual degrees of freedom and at least one free parameter")
    has_expr = any(p["expr"] for p in params.values())
    backend = spec.get("backend", "auto")
    if backend == "auto":
        backend = "odrpack" if np.any(ux) else "lmfit" if has_expr or any(not p["vary"] for p in params.values()) else "scipy"
    if backend not in {"scipy", "lmfit", "odrpack"}:
        raise ValueError("backend must be auto/scipy/lmfit/odrpack")
    if np.any(ux) and backend != "odrpack":
        raise ValueError("nonzero ux requires odrpack; other backends cannot silently ignore it")
    if has_expr and backend != "lmfit":
        raise ValueError("parameter expressions need lmfit; for ODR substitute the relation into the model expression")
    if backend == "odrpack" and (not weighted or covariance_y is not None):
        raise ValueError("ODR adapter requires per-point uy; cross-observation cov_y is not supported")
    limit = spec.get("max_evaluations", 3000)
    if type(limit) is not int or limit <= 0:
        raise ValueError("max_evaluations must be a positive integer")
    return x, y, ux, uy, covariance_y, cholesky, weighted, absolute, model_id, source, f, params, free, backend, limit


def fit_model(spec):
    x, y, ux, uy, cov_y, chol, weighted, absolute, model_id, source, function, params, free, backend, limit = _setup(spec)
    base = {name: p["value"] for name, p in params.items()}
    initial = [base[name] for name in free]
    bounds = ([params[name]["min"] for name in free], [params[name]["max"] for name in free])
    notes, diagnostic = [], {}

    def eval_dict(xdata, values):
        result = np.broadcast_to(np.asarray(function(x=xdata, **values), dtype=float), np.shape(xdata))
        if not np.isfinite(result).all():
            raise ValueError("model evaluated to nonfinite values")
        return result

    def func(xdata, *beta):
        return eval_dict(xdata, {**base, **dict(zip(free, beta))})

    if backend == "scipy":
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            beta, cov, info, message, status = curve_fit(
                func, x, y, p0=initial, sigma=cov_y if cov_y is not None else uy,
                absolute_sigma=absolute, bounds=bounds, method="trf", jac="3-point",
                max_nfev=limit, full_output=True)
        notes.extend(str(w.message) for w in captured)
        values = {**base, **dict(zip(free, beta))}
        success = status in (1, 2, 3, 4)
        objective = float(np.sum(solve_triangular(chol, y-func(x, *beta), lower=True)**2))
        diagnostic.update(nfev=info.get("nfev"), message=message, solver_status=int(status))
    elif backend == "lmfit":
        try:
            import lmfit
        except ImportError as exc:
            raise RuntimeError("lmfit unavailable; install the project requirements, no backend substitution performed") from exc
        parameters = lmfit.Parameters()
        for name, p in params.items():
            parameters.add(name, value=p["value"], min=p["min"], max=p["max"], vary=p["vary"])
        for name, p in params.items():
            if p["expr"]:
                parameters[name].set(expr=p["expr"])

        def residual(current):
            return solve_triangular(chol, y-eval_dict(x, current.valuesdict()), lower=True)

        result = lmfit.minimize(residual, parameters, method="least_squares", scale_covar=not absolute, max_nfev=limit)
        values = dict(result.params.valuesdict())
        free = list(result.var_names)
        cov = result.covar
        beta = [values[name] for name in free]
        success, objective = bool(result.success), float(result.chisqr)
        diagnostic.update(nfev=result.nfev, message=result.message,
                          parameter_errors={name: p.stderr for name, p in result.params.items()})
        if any(p["expr"] for p in params.values()):
            notes.append("Covariance order includes only independent free parameters; dependent values must retain their expressions.")
    else:
        try:
            import odrpack
        except ImportError as exc:
            raise RuntimeError("odrpack unavailable; install the project requirements, x uncertainty was not ignored") from exc
        result = odrpack.odr_fit(lambda xdata, beta: func(xdata, *beta), x, y, np.array(initial),
                                weight_x=1/np.where(ux > 0, ux, 1.0)**2, weight_y=1/uy**2,
                                fix_x=ux == 0, bounds=tuple(np.array(b) for b in bounds),
                                maxit=limit, report="none")
        beta = result.beta
        cov = result.cov_beta if absolute else result.cov_beta*result.res_var
        values = {**base, **dict(zip(free, beta))}
        success, objective = bool(result.success), float(result.sum_square)
        diagnostic.update(nfev=int(result.nfev), message=result.stopreason, solver_status=int(result.info),
                          x_adjustments=result.delta.tolist(), y_adjustments=result.eps.tolist(),
                          residual_variance=float(result.res_var),
                          sd_beta_scaled_by_residual_variance=result.sd_beta.tolist())
        notes.append("ODR covariance uses the declared absolute/relative observation variance convention; objective includes x and y adjustments.")
    predicted = eval_dict(x, values)
    residuals = y-predicted
    dof = len(x)-len(free)
    cov = np.asarray(cov, dtype=float) if cov is not None else None
    valid_cov = cov is not None and cov.shape == (len(free), len(free)) and np.isfinite(cov).all() and np.all(np.diag(cov) >= 0)

    def resolve_values(free_values):
        if backend == "lmfit":
            current = result.params.copy()
            for name, value in zip(free, free_values):
                current[name].value = value
            current.update_constraints()
            return current.valuesdict()
        return {**base, **dict(zip(free, free_values))}
    jac_columns = []
    for i, value in enumerate(beta):
        step = np.cbrt(np.finfo(float).eps)*max(abs(value), 1.0)
        high, low = np.array(beta, dtype=float), np.array(beta, dtype=float)
        high[i], low[i] = value+step, value-step
        jac_columns.append((eval_dict(x, resolve_values(high))-eval_dict(x, resolve_values(low)))/(2*step))
    jac = solve_triangular(chol, np.column_stack(jac_columns), lower=True)
    # Scale columns before rank detection so parameters with different units are
    # not mistaken for rank deficiency merely because of their magnitude.
    column_norms = np.linalg.norm(jac, axis=0)
    if not np.isfinite(column_norms).all() or np.any(column_norms == 0):
        rank, condition = 0, float("inf")
    else:
        singular_values = np.linalg.svd(jac/column_norms, compute_uv=False)
        rank_tolerance = 1e-10*float(singular_values[0])
        rank = int(np.sum(singular_values > rank_tolerance))
        condition = float(singular_values[0]/singular_values[-1]) if singular_values[-1] else float("inf")
    active_bounds = [name for name in free if any(np.isfinite(bound) and abs(values[name]-bound) <= 1e-6*max(1, abs(values[name]))
                     for bound in (params[name]["min"], params[name]["max"]))]
    reliable = bool(success and valid_cov and rank == len(free) and not active_bounds)
    if rank < len(free):
        notes.append("Local Jacobian is rank deficient; parameter uncertainties are not identifiable.")
    if active_bounds:
        notes.append("Parameters at bounds: local symmetric covariance needs further validation.")
    if not valid_cov:
        notes.append("Parameter covariance unavailable/nonfinite; no uncertainty estimate was invented.")
    return {"model": source, "model_id": model_id, "backend": backend,
            "backend_version": importlib.metadata.version(backend), "success": success,
            "parameters": {name: float(value) for name, value in values.items()}, "parameter_order": free,
            "parameter_constraints": {name: {k: v for k, v in p.items() if k in {"vary", "expr"}} for name, p in params.items()},
            "covariance": cov.tolist() if valid_cov else None, "covariance_reliable": reliable,
            "covariance_convention": "absolute_observation_variances" if absolute else "residual_scaled",
            "standard_uncertainties": np.sqrt(np.diag(cov)).tolist() if valid_cov else None,
            "predicted": predicted.tolist(), "residuals": residuals.tolist(), "degrees_of_freedom": dof,
            "weighted": weighted, "absolute_sigma": absolute, "chi2_reduced": objective/dof if weighted else None,
            "residual_std": float(np.sqrt(np.sum(residuals**2)/dof)),
            "jacobian_rank": rank, "condition_number": condition if np.isfinite(condition) else None,
            "active_bounds": active_bounds, "diagnostics": diagnostic, "x_unit": spec["x_unit"], "y_unit": spec["y_unit"],
            "notes": notes+["Covariance is local to the chosen model and assumptions; convergence alone does not validate the physical model."]}


def fit_linear(spec):
    """Compatibility entry point; explicit SciPy linear fit (no x-error omission)."""
    return fit_model({**spec, "model": "linear", "backend": "scipy"})
