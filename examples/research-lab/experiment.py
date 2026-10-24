"""Reproduce the research-film evidence. No UI, network, model calls or secrets.

Host SciPy: synthetic reference, noise, derivative estimation and regression.
Chaos: RK4 integration of every candidate, trajectory error and vector-field error.
Run from any directory with an environment containing numpy/scipy:
  python3 examples/research-lab/experiment.py
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import platform
import sys
import time

import numpy as np
import scipy
from scipy.integrate import solve_ivp
from scipy.signal import savgol_filter

HERE = Path(__file__).resolve().parent
ROOT = next(p for p in HERE.parents if (p / "chaos" / "framework.py").is_file())
sys.path.insert(0, str(ROOT))
from chaos import Framework

SEED = 284
DT = 0.005
TRAIN_END = 30.0
TEST_END = 2.0
NOISE_STD = 0.025
THRESHOLD = 0.1
FEATURES = ["1", "x", "y", "z", "x²", "xy", "xz", "y²", "yz", "z²"]
INITIAL = [1.0, 1.0, 1.0]
TEST_INITIALS = [[-8.0, 7.0, 27.0], [5.0, 5.0, 20.0], [-12.0, -5.0, 35.0]]


def lorenz(_t, u):
    x, y, z = u
    return np.array([10*(y-x), x*(28-z)-y, x*y-(8/3)*z])


def reference(initial, end, dt=DT):
    times = np.linspace(0, end, round(end/dt)+1)
    result = solve_ivp(lorenz, (0, end), initial, t_eval=times,
                       method="DOP853", rtol=1e-11, atol=1e-13)
    if not result.success:
        raise RuntimeError(result.message)
    return times, result.y.T


def library(points):
    x, y, z = points.T
    return np.column_stack([np.ones(len(points)), x, y, z,
                            x*x, x*y, x*z, y*y, y*z, z*z])


def sparse_fit(theta, derivatives):
    coefficients = np.linalg.lstsq(theta, derivatives, rcond=None)[0]
    for _ in range(10):
        coefficients[np.abs(coefficients) < THRESHOLD] = 0
        for axis in range(3):
            active = coefficients[:, axis] != 0
            if np.any(active):
                coefficients[active, axis] = np.linalg.lstsq(
                    theta[:, active], derivatives[:, axis], rcond=None)[0]
    return coefficients


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")


def chaos_validate(coefficients, initial, dt, label):
    times, truth = reference(initial, TEST_END, dt)
    request = {"coefficients": coefficients.T.tolist(), "initial": initial,
               "dt": dt, "steps": len(times)-1,
               "reference": truth.tolist(),
               "reference_field": [lorenz(0, row).tolist() for row in truth]}
    if label == "case_1_quadratic":
        dump(HERE / "input.json", request)
    start = time.perf_counter()
    result = Framework.evaluate(
        (HERE / "validate.chaos").read_text(encoding="utf-8"),
        {"input": request},
        files={"lorenz.chaos": (HERE / "lorenz.chaos").read_text(encoding="utf-8")},
        entry="validate.chaos", step_limit=10000000, timeout=120)
    elapsed = time.perf_counter()-start
    # Independent host reduction checks the Chaos result, not its implementation.
    forecast = np.asarray(result["trajectory"])
    host_rmse = float(np.sqrt(np.mean((forecast-truth)**2)))
    host_relative_l2 = float(np.linalg.norm(library(truth)@coefficients-
        np.array([lorenz(0, row) for row in truth])) /
        np.linalg.norm([lorenz(0, row) for row in truth]))
    assert abs(host_rmse-result["trajectory_rmse"]) < 1e-10
    assert abs(host_relative_l2-result["vector_field_relative_l2"]) < 1e-10
    # Independent high-accuracy integration of the *same candidate* checks RK4.
    host_solution = solve_ivp(lambda _t, state: library(np.array([state]))[0]@coefficients,
        (0, TEST_END), initial, t_eval=times, method="DOP853", rtol=1e-11, atol=1e-13)
    assert host_solution.success
    integration_check = float(np.sqrt(np.mean((forecast-host_solution.y.T)**2)))
    result["label"] = label
    result["host_integration_crosscheck_rmse"] = integration_check
    result["elapsed_seconds_in_this_run"] = elapsed
    dump(HERE / (label+".json"), result)
    return result, times, truth


def main():
    times, clean = reference(INITIAL, TRAIN_END)
    noisy = clean+np.random.default_rng(SEED).normal(0, NOISE_STD, clean.shape)
    smoothed = savgol_filter(noisy, 21, 3, axis=0)
    derivatives = savgol_filter(noisy, 21, 3, axis=0, deriv=1, delta=DT)
    trim = slice(10, -10)
    theta = library(smoothed[trim])
    target = derivatives[trim]
    linear = np.zeros((10, 3))
    linear[:4] = np.linalg.lstsq(theta[:, :4], target, rcond=None)[0]
    quadratic = sparse_fit(theta, target)
    exact = np.zeros((10, 3))
    exact[1, 0], exact[2, 0] = -10, 10
    exact[1, 1], exact[2, 1], exact[6, 1] = 28, -1, -1
    exact[3, 2], exact[5, 2] = -8/3, 1

    training = {}
    for name, coeff in [("linear", linear), ("quadratic", quadratic)]:
        fitted = theta@coeff
        residual = np.sum((target-fitted)**2, axis=0)
        total = np.sum((target-target.mean(axis=0))**2, axis=0)
        training[name] = {"derivative_r2_by_axis": (1-residual/total).tolist(),
            "derivative_relative_l2": float(np.linalg.norm(target-fitted)/np.linalg.norm(target)),
            "active_coefficients": int(np.count_nonzero(coeff)), "coefficients": coeff.T.tolist()}

    arrays = {"train_time": times, "train_clean": clean, "train_noisy": noisy,
              "train_smoothed": smoothed, "estimated_derivatives": derivatives,
              "linear_coefficients": linear, "quadratic_coefficients": quadratic}
    cases = []
    for index, initial in enumerate(TEST_INITIALS, start=1):
        case = {"initial": initial, "horizon": TEST_END, "dt": DT, "models": {}}
        for name, coeff in [("linear", linear), ("quadratic", quadratic)]:
            result, test_t, truth = chaos_validate(coeff, initial, DT, f"case_{index}_{name}")
            arrays[f"case_{index}_{name}"] = np.array(result["trajectory"])
            arrays[f"case_{index}_{name}_error"] = np.array(result["error_curve"])
            case["models"][name] = {key: value for key, value in result.items()
                if key not in {"trajectory", "error_curve", "elapsed_seconds_in_this_run"}}
        arrays[f"case_{index}_reference"] = truth
        arrays["test_time"] = test_t
        cases.append(case)

    coarse, _, _ = chaos_validate(exact, TEST_INITIALS[0], DT, "exact_dt_005")
    fine, _, _ = chaos_validate(exact, TEST_INITIALS[0], DT/2, "exact_dt_0025")
    convergence = {"model": "Known exact Lorenz vector field, no regression",
        "interval": [0, TEST_END], "initial": TEST_INITIALS[0],
        "coarse_dt": DT, "fine_dt": DT/2,
        "coarse_reference_rmse": coarse["trajectory_rmse"],
        "fine_reference_rmse": fine["trajectory_rmse"],
        "error_ratio": coarse["trajectory_rmse"]/fine["trajectory_rmse"],
        "interpretation": "Finite-step check consistent with RK4; not proof of all future-time accuracy."}
    sensitivity_t, base = reference(INITIAL, 40)
    _, perturbed = reference([1.000001, 1.0, 1.0], 40)
    separation = np.linalg.norm(base-perturbed, axis=1)
    crossings = np.flatnonzero(separation >= 1)
    sensitivity = {"initial_delta": 1e-6, "first_distance_at_least_1":
        float(sensitivity_t[crossings[0]]) if len(crossings) else None,
        "integration": "Host SciPy DOP853, rtol=1e-11, atol=1e-13",
        "interpretation": "Illustrates sensitivity in a known chaotic system; not an estimated Lyapunov exponent."}
    arrays.update(sensitivity_time=sensitivity_t, sensitivity_base=base,
                  sensitivity_perturbed=perturbed, sensitivity_separation=separation)
    np.savez_compressed(HERE/"trajectories.npz", **arrays)

    findings = {"schema": "chaos.research-film.lorenz/1", "data_kind": "synthetic benchmark",
        "title": "A model that fits one channel is not a validated dynamical system",
        "parameters": {"sigma": 10, "rho": 28, "beta": 8/3},
        "time_unit": "dimensionless Lorenz time (not physical seconds)",
        "training": {"initial": INITIAL, "interval": [0, TRAIN_END], "samples": len(times),
            "fit_samples": len(theta), "sample_dt": DT, "seed": SEED,
            "noise": {"distribution": "independent additive Gaussian per state/channel",
                      "standard_deviation": NOISE_STD},
            "preprocessing": "Savitzky-Golay window 21, polynomial degree 3; discard 10 endpoint samples each side",
            "derivatives": "Estimated from noisy observations; true equations not used to form training targets",
            "features": FEATURES, "sparsity_threshold_absolute": THRESHOLD,
            "sparsity_iterations": 10, "fits": training},
        "held_out": {"definition": "Three independently initialized trajectories; not used for fitting",
                     "interval": [0, TEST_END], "cases": cases,
                     "trajectory_rmse_definition": "sqrt(mean((predicted-reference)^2)) over all samples and xyz channels",
                     "vector_field_relative_l2_definition": "||f_model(X_ref)-f_true(X_ref)||_F / ||f_true(X_ref)||_F"},
        "step_refinement": convergence, "initial_condition_sensitivity": sensitivity,
        "division_of_work": {"Chaos": ["first-class model closure", "RK4 time stepping", "trajectory RMSE", "held-out vector-field relative L2 error"],
            "host_numpy_scipy": ["synthetic reference", "noise and filtering", "least squares and sparse regression", "independent numerical cross-check", "long-horizon sensitivity display"]},
        "limitations": ["Synthetic, known Lorenz system; not laboratory observations or discovery of unknown physics",
            "Polynomial candidate library and sparsity threshold are chosen assumptions; success depends on these choices and state coverage",
            "Only three held-out initial conditions and a finite horizon are tested; this is not a universal generalization proof",
            "True vector-field error is observable only because this is a synthetic benchmark",
            "Long-horizon separation is expected in chaos and alone cannot establish model failure",
            "No paid model call, autonomous agent scientific decision, GPU execution or new physical discovery is claimed"],
        "sources": [{"title": "Lorenz (1963), Deterministic Nonperiodic Flow", "url": "https://journals.ametsoc.org/doi/abs/10.1175/1520-0469%281963%29020%3C0130%3Adnf%3E2.0.CO%3B2"},
                    {"title": "Brunton, Proctor & Kutz (2016), sparse identification of nonlinear dynamical systems", "url": "https://arxiv.org/abs/1509.03580"}]}
    dump(HERE/"experiment.json", findings)
    visual = {"schema": "chaos.research-film.visual/1", "metrics": findings,
        "train": {"t": times[::3].tolist(), "clean": clean[::3].tolist(), "noisy": noisy[::3].tolist()},
        "test": {"t": arrays["test_time"].tolist(),
                 "reference": arrays["case_1_reference"].tolist(),
                 "linear": arrays["case_1_linear"].tolist(),
                 "quadratic": arrays["case_1_quadratic"].tolist()},
        "sensitivity": {"t": sensitivity_t[::4].tolist(), "base": base[::4].tolist(),
            "perturbed": perturbed[::4].tolist(), "distance": separation[::4].tolist()}}
    dump(HERE/"visual-data.json", visual)
    verification = {"status": "passed", "python": platform.python_version(),
        "numpy": np.__version__, "scipy": scipy.__version__,
        "chaos_sdk": "Framework.evaluate; 8 independent invocations of validate.chaos",
        "core_files_sha256": {name: hashlib.sha256((HERE/name).read_bytes()).hexdigest()
            for name in ["experiment.py", "lorenz.chaos", "validate.chaos", "input.json"]},
        "checks": ["All Chaos RMSE and field reductions match independent NumPy reduction within 1e-10",
                   "Every Chaos candidate trajectory compared with independent high-tolerance SciPy integration",
                   "Known-field RK4 dt / 2 convergence measured independently"]}
    dump(HERE/"verification.json", verification)
    print(json.dumps({"cases": cases, "step_refinement": convergence,
                      "sensitivity": sensitivity, "training": training}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
