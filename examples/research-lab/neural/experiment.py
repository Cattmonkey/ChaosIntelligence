"""Reproducible neural excitability illustration; no model/API/UI calls.

Chaos reuses ../lorenz.chaos RK4; SciPy gives an independent piecewise-smooth
reference. All state, input and time variables are dimensionless.
"""
import hashlib
import json
from pathlib import Path
import sys
import time
import numpy as np
from scipy.integrate import solve_ivp
from scipy.optimize import brentq

HERE = Path(__file__).resolve().parent
ROOT = next(p for p in HERE.parents if (p/"chaos"/"framework.py").is_file())
sys.path.insert(0, str(ROOT))
from chaos import Framework

A, B, TAU, DT, END = .7, .8, 12.5, .05, 60


def dump(name, value):
    (HERE/name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n")


def main():
    v0 = brentq(lambda v: v-v**3/3-(v+A)/B, -2, -1)
    w0 = (v0+A)/B
    grid = np.linspace(0, END, round(END/DT)+1)
    payload = {"schema": "chaos.research-film.fhn/1", "t": grid.tolist()}
    checks = []
    for label, amplitude in [("low", .1), ("high", .8)]:
        initial = np.array([v0, w0])
        segments = []
        for index, (start, stop, current) in enumerate([(0, 10, 0), (10, 11, amplitude), (11, END, 0)]):
            times = np.linspace(start, stop, round((stop-start)/DT)+1)
            reference = solve_ivp(lambda t, s: [s[0]-s[0]**3/3-s[1]+current,
                (s[0]+A-B*s[1])/TAU], (start, stop), initial, t_eval=times,
                method="DOP853", rtol=1e-11, atol=1e-13)
            assert reference.success
            segments.append(reference.y.T if index == 0 else reference.y.T[1:])
            initial = reference.y[:, -1]
        truth = np.vstack(segments)
        assert len(truth) == len(grid)
        request = {"a": A, "b": B, "tau": TAU, "dt": DT, "steps": len(grid)-1,
            "initial": [v0, w0, 0], "amplitude": amplitude,
            "pulse_start_step": 200, "pulse_end_step": 220,
            "reference": np.column_stack([truth, np.zeros(len(truth))]).tolist()}
        start = time.perf_counter()
        result = Framework.evaluate((HERE/"excitation.chaos").read_text(),
            {"input": request},
            files={"lorenz.chaos": (HERE.parent/"lorenz.chaos").read_text()},
            entry="neural/excitation.chaos", step_limit=10000000, timeout=180)
        elapsed = time.perf_counter()-start
        trajectory = np.array(result["trajectory"])[:, :2]
        host_rmse = float(np.sqrt(np.mean((trajectory-truth)**2)))
        assert abs(host_rmse-result["joint_vw_rmse"]) < 1e-12
        assert host_rmse < 1e-4
        peak_index = int(np.argmax(trajectory[:, 0]))
        metrics = {"joint_vw_rmse_vs_scipy": result["joint_vw_rmse"],
            "sampled_peak_v": float(trajectory[peak_index, 0]),
            "sampled_peak_time": float(grid[peak_index]),
            "final_v": float(trajectory[-1, 0]), "final_w": float(trajectory[-1, 1]),
            "host_crosscheck_max_abs_error": float(np.max(np.abs(trajectory-truth)))}
        payload[label] = {"v": trajectory[:, 0].tolist(), "w": trajectory[:, 1].tolist(),
            "input": result["applied_current_per_step"]+[0],
            "reference_v": truth[:, 0].tolist(), "reference_w": truth[:, 1].tolist(),
            "metrics": metrics}
        checks.append({"label": label, "language": "Chaos", "sdk": "Framework.evaluate",
            "wall_seconds": elapsed, "steps": len(grid)-1, "dt": DT, **metrics})
        dump(label+"-chaos-result.json", result)
    payload["metadata"] = {
        "model": "FitzHugh-Nagumo, a chosen dimensionless normal form",
        "equations": ["dv/dt = v - v³/3 - w + I(t)", "dw/dt = (v + a - b*w)/tau"],
        "parameters": {"a": A, "b": B, "tau": TAU}, "dt": DT,
        "initial": [v0, w0], "pulse": [10, 11], "amplitudes": [.1, .8],
        "time_unit": "dimensionless; not seconds or milliseconds",
        "reference": "Piecewise DOP853, rtol=1e-11, atol=1e-13; exact stimulus boundaries",
        "computation": "Both trajectories and RMSE reductions execute in Chaos using the same imported RK4 function as Lorenz",
        "limitations": ["A simplified qualitative excitability model, not measured neural data",
            "No claim about a real neuron, brain, consciousness, diagnosis or treatment",
            "The two selected pulse amplitudes illustrate different responses; no exact firing threshold is estimated",
            "This normal form and tau=12.5 are illustrative choices, not a literal parameter replication of FitzHugh's 1961 paper"],
        "sources": [{"title": "FitzHugh (1961), Impulses and Physiological States in Theoretical Models of Nerve Membrane",
            "doi": "10.1016/S0006-3495(61)86902-6", "url": "https://jxshix.people.wm.edu/2009-harbin-course/classic/FitzHugh-1961.pdf",
            "note": "Original paper, university-hosted scan; qualitative two-state excitability and recovery model"},
            {"title": "Nagumo, Arimoto & Yoshizawa (1962), An Active Pulse Transmission Line Simulating Nerve Axon",
             "doi": "10.1109/JRPROC.1962.288235", "url": "https://doi.org/10.1109/JRPROC.1962.288235"}]}
    dump("visual-data.json", payload)
    dump("verification.json", {"status": "passed", "model_calls": 0, "checks": checks,
        "rk4_source_sha256": hashlib.sha256((HERE.parent/"lorenz.chaos").read_bytes()).hexdigest(),
        "neural_source_sha256": hashlib.sha256((HERE/"excitation.chaos").read_bytes()).hexdigest()})
    print(json.dumps({"checks": checks, "metadata": payload["metadata"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
