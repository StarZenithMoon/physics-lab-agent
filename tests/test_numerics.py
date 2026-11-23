import copy
import csv
import math
from pathlib import Path
import tempfile
import unittest
import numpy as np
from labcore.data import profile_csv
from labcore.fit import fit_linear
from labcore.uncertainty import analytic, monte_carlo, assess_component, propagate
from labcore.reporting import rounded_standard


def spec(model="sum", rho=None):
    value = {"model_id": model, "inputs": {
        "x": {"value": 2.0, "u": .1, "unit": "1", "distribution": "normal", "source": "test"},
        "z": {"value": 3.0, "u": .2, "unit": "1", "distribution": "normal", "source": "test"}},
        "independent": rho is None, "assumptions": ["synthetic test"],
        "mc": {"batches": 5, "samples_per_batch": 20000, "seed": 23}}
    if rho is not None:
        value["correlation_matrix"] = [[1, rho], [rho, 1]]
    return value


class UncertaintyTests(unittest.TestCase):
    def test_repeat_mean_and_resolution_are_different_assessments(self):
        result = assess_component({"kind": "repeat_mean", "values": [1, 2, 3], "independent": True})
        self.assertAlmostEqual(result["u"], 1/math.sqrt(3))
        self.assertEqual(result["dof"], 2)
        self.assertAlmostEqual(assess_component({"kind": "resolution", "d": 1})["u"], 1/math.sqrt(12))
        with self.assertRaises(ValueError):
            assess_component({"kind": "repeat_mean", "values": [1, 2, 3]})

    def test_linear_sum_matches_known_variance_and_mc(self):
        p = propagate(spec())
        self.assertAlmostEqual(p["analytic"]["u"], math.sqrt(.1**2+.2**2))
        self.assertLess(abs(p["mc"]["u"]-p["analytic"]["u"])/p["analytic"]["u"], .015)
        self.assertEqual(p["comparison"]["status"], "diagnostic_only")

    def test_covariance_sign_and_common_mode_cancellation(self):
        value = spec("difference", .8)
        value["inputs"]["z"]["u"] = .1
        p = propagate(value)
        self.assertAlmostEqual(p["analytic"]["u"]**2, .004)
        self.assertLess(p["analytic"]["covariance_terms"][0]["variance_contribution"], 0)
        self.assertAlmostEqual(p["mc"]["u"], math.sqrt(.004), delta=.001)
        value["correlation_matrix"] = [[1, 1], [1, 1]]
        self.assertAlmostEqual(analytic(value)["u"], 0)
        self.assertLess(monte_carlo(value)["u"], 1e-14)

    def test_zero_gradient_does_not_make_sampling_variance_zero(self):
        value = spec("product")
        for v in value["inputs"].values():
            v.update(value=0.0, u=1.0)
        p = propagate(value)
        self.assertEqual(p["analytic"]["u"], 0)
        self.assertAlmostEqual(p["mc"]["u"], 1, delta=.03)
        self.assertIsNone(p["comparison"]["relative_u_difference"])

    def test_t_uses_standard_deviation_not_scale(self):
        value = spec()
        value.update(model_id="identity")
        value["inputs"] = {"x": {**value["inputs"]["x"], "distribution": "t", "df": 10, "u": 2}}
        self.assertAlmostEqual(monte_carlo(value)["u"], 2, delta=.04)
        value["inputs"]["x"]["df"] = 2
        with self.assertRaises(ValueError):
            monte_carlo(value)

    def test_invalid_or_contradictory_correlations_fail(self):
        for matrix in ([[1, 1.1], [1.1, 1]], [[1, .3], [.1, 1]], [[1, float("nan")], [float("nan"), 1]]):
            value = spec(rho=.5)
            value["correlation_matrix"] = matrix
            with self.assertRaises(ValueError):
                analytic(value)
        value = spec(rho=.5)
        value["independent"] = True
        with self.assertRaises(ValueError):
            analytic(value)
        value = spec(rho=.5)
        value["inputs"]["x"]["distribution"] = "uniform"
        with self.assertRaises(ValueError):
            monte_carlo(value)

    def test_coverage_requires_basis_and_is_not_assumed(self):
        value = spec()
        self.assertNotIn("expanded", analytic(value))
        value["coverage_factor"] = 2
        with self.assertRaises(ValueError):
            analytic(value)
        value["coverage_basis"] = "explicit test assumption"
        self.assertAlmostEqual(analytic(value)["expanded"]["U"], 2*math.sqrt(.05))

    def test_physical_units_and_invalid_samples_are_not_silently_fixed(self):
        value = {"model_id": "pendulum_g", "independent": True, "assumptions": ["test"], "inputs": {
            "L": {"value": 1, "u": .01, "unit": "m", "distribution": "uniform", "source": "test"},
            "T": {"value": 1, "u": 1, "unit": "s", "distribution": "uniform", "source": "test"}}}
        with self.assertRaises(ValueError):
            monte_carlo(value)
        value["inputs"]["L"]["unit"] = "mm"
        with self.assertRaises(ValueError):
            analytic(value)

    def test_density_explicit_gradient(self):
        value = {"model_id": "cylinder_density", "independent": True, "assumptions": ["test"], "inputs": {
            "m": {"value": .1, "u": .0001, "unit": "kg", "distribution": "uniform", "source": "test"},
            "D": {"value": .02, "u": .00001, "unit": "m", "distribution": "uniform", "source": "test"},
            "H": {"value": .04, "u": .00002, "unit": "m", "distribution": "uniform", "source": "test"}}}
        actual = analytic(value)
        expected_value = .1/(math.pi*(.02/2)**2*.04)
        expected_u = expected_value*math.sqrt((.0001/.1)**2+(2*.00001/.02)**2+(.00002/.04)**2)
        self.assertAlmostEqual(actual["estimate"], expected_value)
        self.assertAlmostEqual(actual["u"], expected_u)

    def test_reproducible_seeds_and_rounding_carry(self):
        self.assertEqual(monte_carlo(spec()), monte_carlo(spec()))
        self.assertEqual(rounded_standard(12.34567, .0999), ("12.35", "0.10"))

    def test_report_rounding_uses_half_even_for_both_numbers(self):
        for value, expected in [("2.4500", "2.4"), ("2.5500", "2.6"),
                                ("2.4501", "2.5"), ("-2.45", "-2.4")]:
            with self.subTest(value=value):
                self.assertEqual(rounded_standard(value, "1.2"), (expected, "1.2"))
        self.assertEqual(rounded_standard("1.2350", "0.00025", digits=1), ("1.2350", "0.0002"))
        self.assertEqual(rounded_standard(1234, 99.9), ("1.23E+3", "1.0E+2"))

    def test_report_alignment_preserves_recorded_precision(self):
        self.assertEqual(rounded_standard("12.3", ".042"), ("12.300", "0.042"))
        self.assertEqual(rounded_standard("12.3", ".042", recorded_value="12.3"), ("12.30", "0.04"))
        self.assertEqual(rounded_standard("12.3", ".042", recorded_value="12.300"), ("12.300", "0.042"))
        self.assertEqual(rounded_standard("12.3", ".042", recorded_value="12.3", digits=2), ("12.300", "0.042"))
        with self.assertRaises(ValueError):
            rounded_standard("12.3", ".042", recorded_value="12.4")


class DataAndFitTests(unittest.TestCase):
    def test_profile_keeps_rows_and_flags_invalid_cells(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"data.csv"
            path.write_text("x,y\n1,2\n1,2\n3,NaN\n4,\n", encoding="utf-8")
            result = profile_csv(path)
        self.assertEqual(result["rows"], 4)
        self.assertEqual(result["columns"]["y"]["n"], 2)
        self.assertEqual([v["row"] for v in result["columns"]["y"]["invalid"]], [4, 5])
        self.assertEqual(len(result["duplicate_rows"]), 1)

    def test_known_sigma_covariance_is_not_scaled_by_residuals(self):
        value = {"x": [0, 1, 2, 3], "y": [1, 3, 5, 7], "uy": [.1]*4,
                 "x_unit": "A", "y_unit": "V"}
        fit = fit_linear(value)
        self.assertAlmostEqual(fit["parameters"]["slope"], 2)
        self.assertAlmostEqual(fit["covariance"][1][1], .1**2/5)
        self.assertLess(fit["covariance"][0][1], 0)
        self.assertTrue(fit["absolute_sigma"])

    def test_rank_deficiency_and_x_error_are_not_ignored(self):
        for extra in ({"x": [1, 1, 1]}, {"ux": [.1]*3}):
            with self.assertRaises(ValueError):
                fit_linear({"x": [1, 2, 3], "y": [1, 2, 3], "x_unit": "A", "y_unit": "V", **extra})


if __name__ == "__main__":
    unittest.main()
