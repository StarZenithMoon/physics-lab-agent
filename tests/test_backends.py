import copy
import importlib.util
import json
import unittest
import numpy as np
from labcore.fit import fit_model
from labcore.uncertainty import propagate, monte_carlo


def linear(**extra):
    return {"x": [0, 1, 2, 3, 4], "y": [1.05, 2.92, 5.10, 6.96, 8.97],
            "uy": [.1]*5, "x_unit": "A", "y_unit": "V", **extra}


def mc_spec(**extra):
    return {"model_id": "difference", "independent": False, "correlation_matrix": [[1, .8], [.8, 1]],
            "inputs": {"x": {"value": 3, "u": .1, "unit": "1", "distribution": "normal", "source": "test"},
                       "z": {"value": 2, "u": .1, "unit": "1", "distribution": "normal", "source": "test"}},
            "assumptions": ["synthetic benchmark"],
            "mc": {"batches": 3, "samples_per_batch": 5000, "seed": 99, **extra}}


class FitBackendTests(unittest.TestCase):
    def test_all_backends_preserve_known_absolute_sigma(self):
        results = [fit_model(linear(backend=b)) for b in ["scipy", "lmfit", "odrpack"]]
        design = np.column_stack([np.ones(5), np.arange(5)])
        reference = .1**2*np.linalg.inv(design.T@design)
        for result in results:
            self.assertTrue(result["success"])
            self.assertTrue(result["covariance_reliable"])
            np.testing.assert_allclose(result["covariance"], reference, rtol=1e-5, atol=1e-9)
            self.assertEqual(result["covariance_convention"], "absolute_observation_variances")
            json.dumps(result, allow_nan=False)
        np.testing.assert_allclose(list(results[0]["parameters"].values()), list(results[2]["parameters"].values()), rtol=1e-5)

    def test_residual_scaled_covariance_is_explicit_across_backends(self):
        for backend in ["scipy", "lmfit", "odrpack"]:
            absolute = fit_model(linear(backend=backend))
            relative = fit_model(linear(backend=backend, absolute_sigma=False))
            np.testing.assert_allclose(relative["covariance"], np.asarray(absolute["covariance"])*absolute["chi2_reduced"], rtol=1e-4)
            self.assertEqual(relative["covariance_convention"], "residual_scaled")

    def test_nonlinear_scipy_decay(self):
        rng = np.random.default_rng(10)
        x = np.linspace(0, 5, 40)
        y = .3+2*np.exp(-x/1.2)+rng.normal(0, .01, len(x))
        result = fit_model({"model": "exponential_decay", "x": x.tolist(), "y": y.tolist(), "uy": [.01]*len(x),
                            "parameters": {"offset": .2, "amplitude": 1.5, "tau": {"value": 1, "min": .001}},
                            "x_unit": "s", "y_unit": "V"})
        self.assertEqual(result["backend"], "scipy")
        self.assertAlmostEqual(result["parameters"]["tau"], 1.2, delta=.05)
        self.assertTrue(result["covariance_reliable"])

    def test_lmfit_constraint_keeps_only_free_parameter_covariance(self):
        x = np.arange(5.)
        result = fit_model({**linear(), "x": x.tolist(), "y": (1+2*x).tolist(),
                            "parameters": {"intercept": {"value": .5, "expr": "slope/2"}, "slope": 1}})
        self.assertEqual(result["backend"], "lmfit")
        self.assertEqual(result["parameter_order"], ["slope"])
        self.assertAlmostEqual(result["parameters"]["intercept"], result["parameters"]["slope"]/2)
        self.assertAlmostEqual(result["covariance"][0][0], .1**2/np.sum((x+.5)**2))

    def test_named_fixed_parameter_and_custom_expression(self):
        x = np.linspace(0, 3, 20)
        result = fit_model({"model": "expression", "expression": "offset+amplitude*exp(-x/tau)",
                            "parameters": {"offset": {"value": .2, "vary": False}, "amplitude": 1., "tau": {"value": 1., "min": .01}},
                            "x": x.tolist(), "y": (.2+2*np.exp(-x/1.5)).tolist(), "uy": [.1]*len(x), "x_unit": "s", "y_unit": "V"})
        self.assertEqual(result["backend"], "lmfit")
        self.assertEqual(result["parameter_order"], ["amplitude", "tau"])
        self.assertAlmostEqual(result["parameters"]["tau"], 1.5, places=5)

    def test_odr_matches_deming_solution_and_records_x_adjustments(self):
        x = np.array([0., 1.1, 1.9, 3.2, 3.8])
        y = np.array([1., 3.1, 4.8, 7.2, 9.])
        result = fit_model({**linear(), "x": x.tolist(), "y": y.tolist(), "ux": [.1]*len(x)})
        xx, yy = x-x.mean(), y-y.mean()
        sxx, syy, sxy = np.sum(xx**2), np.sum(yy**2), np.sum(xx*yy)
        reference_slope = (syy-sxx+np.sqrt((syy-sxx)**2+4*sxy**2))/(2*sxy)
        self.assertEqual(result["backend"], "odrpack")
        self.assertAlmostEqual(result["parameters"]["slope"], reference_slope, places=4)
        self.assertTrue(any(abs(v) > 1e-4 for v in result["diagnostics"]["x_adjustments"]))
        np.testing.assert_allclose(np.sqrt(np.diag(result["covariance"])*result["diagnostics"]["residual_variance"]),
                                   result["diagnostics"]["sd_beta_scaled_by_residual_variance"], rtol=1e-5)

    def test_full_y_covariance_is_used_by_scipy_and_lmfit(self):
        covariance = np.eye(5)*.01+np.ones((5, 5))*.002
        value = linear(cov_y=covariance.tolist())
        del value["uy"]
        design = np.column_stack([np.ones(5), np.arange(5)])
        expected = np.linalg.inv(design.T@np.linalg.solve(covariance, design))
        for backend in ["scipy", "lmfit"]:
            result = fit_model({**value, "backend": backend})
            np.testing.assert_allclose(result["covariance"], expected, rtol=1e-5)

    def test_x_error_covariance_and_unknown_backends_are_not_ignored(self):
        for value in [linear(backend="scipy", ux=[.1]*5), linear(backend="unknown"),
                      linear(cov_xy=np.eye(5).tolist()), linear(uy=[0]*5)]:
            with self.assertRaises(ValueError):
                fit_model(value)
        with self.assertRaises(ValueError):
            fit_model({**linear(), "backend": "odrpack", "cov_y": np.eye(5).tolist()})

    def test_unidentifiable_parameters_and_bounds_are_flagged(self):
        common = {**linear(), "model": "expression", "expression": "a+b*x+c*x", "parameters": {"a": 0, "b": 1, "c": 1}}
        self.assertFalse(fit_model(common)["covariance_reliable"])
        bound = fit_model({**linear(), "parameters": {"intercept": 1, "slope": {"value": .5, "min": 0, "max": 1}}})
        self.assertIn("slope", bound["active_bounds"])
        self.assertFalse(bound["covariance_reliable"])

    def test_model_expression_rejects_arbitrary_python(self):
        value = {**linear(), "model": "expression", "parameters": {"a": 1}}
        for code in ["__import__('os').system('whoami')", "x.__class__", "[a for a in x]", "unknown(x)"]:
            with self.assertRaises(ValueError):
                fit_model({**value, "expression": code})


@unittest.skipUnless(importlib.util.find_spec("punpy"), "optional Punpy dependency not installed")
class PunpyBackendTests(unittest.TestCase):
    def test_shared_sample_backend_equivalence_for_correlated_normal(self):
        numpy_result = monte_carlo(mc_spec())
        punpy_result = monte_carlo(mc_spec(backend="punpy"))
        self.assertEqual(punpy_result["backend"], "punpy")
        self.assertAlmostEqual(numpy_result["u"], punpy_result["u"], places=12)
        self.assertEqual(punpy_result["samples"], numpy_result["samples"])
        np.testing.assert_allclose(punpy_result["interval"], numpy_result["interval"])

    def test_mixed_distributions_t_and_seed_reproducibility(self):
        value = mc_spec(backend="punpy")
        value.update(independent=True)
        del value["correlation_matrix"]
        value["inputs"]["x"]["distribution"] = "uniform"
        value["inputs"]["z"].update(distribution="t", df=10)
        one, two = monte_carlo(value), monte_carlo(value)
        self.assertEqual(one, two)
        self.assertAlmostEqual(one["u"], np.sqrt(.02), delta=.005)

    def test_invalid_model_outputs_cannot_be_filtered_by_punpy(self):
        value = mc_spec(backend="punpy")
        value.update(model_id="ratio", independent=True)
        del value["correlation_matrix"]
        value["inputs"]["z"].update(value=0., u=0.)
        with self.assertRaises(ValueError):
            propagate(value)

    def test_deterministic_inputs_and_sample_count(self):
        value = mc_spec(backend="punpy")
        for v in value["inputs"].values():
            v["u"] = 0
        result = monte_carlo(value)
        self.assertEqual(result["u"], 0)
        self.assertEqual(result["samples"], 15000)


if __name__ == "__main__":
    unittest.main()
