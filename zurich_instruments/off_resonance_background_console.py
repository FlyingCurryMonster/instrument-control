"""Console acquisition of Zurich off-resonance background points.

This is a PyMeasure console procedure, not a dock-window UI. It commands a
Zurich oscillator through a list of requested drive/frequency points, polls the
demodulator at each point, and emits one averaged row per point. The emitted
columns are compatible with the ``bkg_pts`` table used in
``freq-sweeps-bkg-compare.ipynb``.

Target CSV columns:
    Required: ``V_drive`` and ``f_drive``
    Optional metadata: ``temperature``, ``left_right``, ``peak_R``

The aliases ``drive_voltage`` and ``drive_frequency`` are also accepted.
"""

from __future__ import annotations

import logging
import re
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd
import zhinst.core
from pymeasure.display.console import ManagedConsole
from pymeasure.experiment import FloatParameter, IntegerParameter, Parameter, Procedure

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

DEFAULT_RESULT_DIR = Path(r"D:/Data/Fall25-Summer26/TO off-resonance background")


def parse_si_value(value) -> float:
    """Parse floats and common unit suffixes used in target CSV files."""
    if pd.isna(value):
        return np.nan
    if isinstance(value, (int, float, np.number)):
        return float(value)

    text = str(value).strip()
    match = re.fullmatch(
        r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([A-Za-z]*)",
        text,
    )
    if not match:
        raise ValueError(f"Could not parse numeric value: {value!r}")

    number = float(match.group(1))
    unit = match.group(2).lower()
    scale = {
        "": 1.0,
        "v": 1.0,
        "mv": 1e-3,
        "uv": 1e-6,
        "hz": 1.0,
        "khz": 1e3,
        "mk": 1.0,
    }.get(unit)
    if scale is None:
        raise ValueError(f"Unsupported unit suffix in {value!r}")
    return number * scale


class OffResonanceBackgroundProcedure(Procedure):
    """Measure averaged X/Y background at explicitly requested off-resonance points."""

    target_csv_path = Parameter("Target csv path", default="")
    drive_voltages = Parameter("Drive voltages (comma/space)", default="")
    drive_frequencies = Parameter("Drive frequencies (comma/space)", default="")
    resonance_frequency = FloatParameter(
        "Resonance frequency for left/right labels", units="Hz", default=0.0
    )

    settle_time = FloatParameter("Settle time", units="s", default=10.0)
    sample_duration = FloatParameter("Sample duration", units="s", default=5.0)
    poll_interval = FloatParameter("Poll interval", units="s", default=0.5)
    min_samples = IntegerParameter("Minimum samples", default=3)

    zur_id = Parameter("Zurich addr.", default="dev4934")
    osc_num = IntegerParameter("Oscillator number", default=2)
    demod_num = IntegerParameter("Demodulator number", default=1)
    server_host = Parameter("Server host", default="192.168.77.26")
    server_port = IntegerParameter("Server port", default=8004)
    interface = Parameter("Interface", default="PCIe")
    comments = Parameter("Comments/Notes", default="")

    PARAMETERS = [
        "target_csv_path",
        "drive_voltages",
        "drive_frequencies",
        "resonance_frequency",
        "settle_time",
        "sample_duration",
        "poll_interval",
        "min_samples",
        "zur_id",
        "osc_num",
        "demod_num",
        "server_host",
        "server_port",
        "interface",
        "comments",
    ]

    DATA_COLUMNS = [
        "step_index",
        "temperature",
        "left_right",
        "f_drive",
        "peak_R",
        "V_drive",
        "x_bkg",
        "x_bkg_std",
        "y_bkg",
        "y_bkg_std",
        "r_bkg",
        "phase_bkg",
        "n_samples",
        "sample_duration",
        "settle_time",
        "frequency_readback",
        "frequency_measured_mean",
        "frequency_measured_std",
        "voltage_readback",
        "demod_phase_readback",
        "status",
        "target_label",
        "utc_start",
        "utc_end",
    ]

    def startup(self):
        log.info("Starting Zurich off-resonance background acquisition")
        self._validate_parameters()

        self.osc_index = self.osc_num - 1
        self.demod_index = self.demod_num - 1

        self.targets = self._load_targets()
        self.total_steps = len(self.targets)
        if self.total_steps == 0:
            raise ValueError("No background target points were specified.")

        self.daq = zhinst.core.ziDAQServer(
            self.server_host, int(self.server_port), api_level=6
        )
        self.daq.connectDevice(self.zur_id, interface=self.interface)
        self.sample_path = f"/{self.zur_id}/demods/{self.demod_index}/sample"
        self.daq.set(f"/{self.zur_id}/demods/{self.demod_index}/enable", 1)

        self.restore_amp = self._get_amp()
        self.restore_freq = self._get_freq()
        log.info("Loaded %d background targets", self.total_steps)

    def execute(self):
        for step_index, target in enumerate(self.targets):
            if self.should_stop():
                log.warning("Stop requested before step %d", step_index)
                break

            data = self._run_target(step_index, target)
            self.emit("results", data)
            self.emit("progress", 100.0 * (step_index + 1) / max(1, self.total_steps))

        self.emit("progress", 100.0)

    def shutdown(self):
        log.info("Restoring original Zurich drive settings")
        try:
            if hasattr(self, "restore_amp") and hasattr(self, "restore_freq"):
                self._set_drive(self.restore_amp, self.restore_freq)
        except Exception:
            log.exception("Unable to restore original Zurich drive settings")
        log.info("Finished off-resonance background acquisition")

    def check_parameters(self):
        self._validate_parameters()

    def _validate_parameters(self):
        if self.osc_num <= 0 or self.demod_num <= 0:
            raise ValueError("osc_num and demod_num are 1-based and must be > 0.")
        if self.settle_time < 0:
            raise ValueError("settle_time must be >= 0.")
        if self.sample_duration <= 0:
            raise ValueError("sample_duration must be > 0.")
        if self.poll_interval <= 0:
            raise ValueError("poll_interval must be > 0.")
        if self.min_samples <= 0:
            raise ValueError("min_samples must be > 0.")

        has_csv = bool(str(self.target_csv_path).strip())
        has_lists = bool(str(self.drive_voltages).strip()) or bool(
            str(self.drive_frequencies).strip()
        )
        if not has_csv and not has_lists:
            raise ValueError(
                "Provide either target_csv_path or drive_voltages/drive_frequencies."
            )
        if has_lists:
            voltages = self._parse_list(self.drive_voltages, "drive_voltages")
            frequencies = self._parse_list(self.drive_frequencies, "drive_frequencies")
            if len(voltages) != len(frequencies):
                raise ValueError(
                    "drive_voltages and drive_frequencies must have equal length."
                )
            if any(v < 0 for v in voltages):
                raise ValueError("drive_voltages must be >= 0.")
            if any(f <= 0 for f in frequencies):
                raise ValueError("drive_frequencies must be > 0.")

    def _load_targets(self) -> List[Dict[str, object]]:
        csv_path = str(self.target_csv_path).strip()
        if csv_path:
            targets = self._load_targets_from_csv(Path(csv_path).expanduser())
        else:
            targets = self._load_targets_from_lists()

        for idx, target in enumerate(targets):
            if self._is_blank(target.get("left_right")):
                target["left_right"] = self._infer_left_right(float(target["f_drive"]))
            if self._is_blank(target.get("target_label")):
                target["target_label"] = f"target_{idx}"
        return targets

    def _load_targets_from_csv(self, path: Path) -> List[Dict[str, object]]:
        if not path.exists():
            raise ValueError(f"Target CSV not found: {path}")

        df = pd.read_csv(path)
        df.columns = [str(col).strip() for col in df.columns]
        alias_map = {
            "drive_voltage": "V_drive",
            "drive_frequency": "f_drive",
            "voltage": "V_drive",
            "frequency": "f_drive",
            "peak R": "peak_R",
            "label": "target_label",
        }
        for alias, canonical in alias_map.items():
            if alias not in df.columns:
                continue
            if canonical in df.columns:
                df[canonical] = df[canonical].where(df[canonical].notna(), df[alias])
                df = df.drop(columns=[alias])
            else:
                df = df.rename(columns={alias: canonical})

        required = {"V_drive", "f_drive"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"Target CSV missing columns: {sorted(missing)}")

        for col in ["V_drive", "f_drive", "peak_R", "temperature"]:
            if col in df.columns:
                df[col] = df[col].map(parse_si_value)
        if "left_right" not in df.columns:
            df["left_right"] = ""
        else:
            df["left_right"] = df["left_right"].fillna("")
        if "peak_R" not in df.columns:
            df["peak_R"] = np.nan
        if "temperature" not in df.columns:
            df["temperature"] = np.nan
        if "target_label" not in df.columns:
            df["target_label"] = ""
        else:
            df["target_label"] = df["target_label"].fillna("")

        targets = []
        for _, row in df.iterrows():
            f_drive = float(row["f_drive"])
            v_drive = float(row["V_drive"])
            if v_drive < 0:
                raise ValueError("Target V_drive entries must be >= 0.")
            if f_drive <= 0:
                raise ValueError("Target f_drive entries must be > 0.")
            targets.append(row.to_dict())
        return targets

    def _load_targets_from_lists(self) -> List[Dict[str, object]]:
        voltages = self._parse_list(self.drive_voltages, "drive_voltages")
        frequencies = self._parse_list(self.drive_frequencies, "drive_frequencies")
        return [
            {
                "temperature": np.nan,
                "left_right": "",
                "f_drive": float(frequency),
                "peak_R": np.nan,
                "V_drive": float(voltage),
                "target_label": "",
            }
            for voltage, frequency in zip(voltages, frequencies)
        ]

    @staticmethod
    def _parse_list(values: object, name: str) -> List[float]:
        text = str(values).strip()
        if not text:
            raise ValueError(f"{name} is required.")
        cleaned = text.strip().strip("[]()")
        parts = [p for p in re.split(r"[,\s]+", cleaned) if p]
        if not parts:
            raise ValueError(f"{name} is required.")
        return [parse_si_value(part) for part in parts]

    def _infer_left_right(self, frequency: float) -> str:
        if self.resonance_frequency <= 0:
            return ""
        if frequency < self.resonance_frequency:
            return "left"
        if frequency > self.resonance_frequency:
            return "right"
        return "center"

    def _run_target(self, step_index: int, target: Dict[str, object]) -> Dict[str, object]:
        voltage = float(target["V_drive"])
        frequency = float(target["f_drive"])

        log.info(
            "Step %d/%d: V_drive=%g V, f_drive=%g Hz",
            step_index + 1,
            self.total_steps,
            voltage,
            frequency,
        )

        self._set_drive(voltage, frequency)
        if self.settle_time > 0:
            self._sleep_with_abort(self.settle_time)

        utc_start = time.time()
        sample = self._poll_samples()
        utc_end = time.time()

        xs = np.asarray(sample.get("x", []), dtype=float)
        ys = np.asarray(sample.get("y", []), dtype=float)
        freqs = np.asarray(sample.get("frequency", []), dtype=float)
        n_samples = min(len(xs), len(ys))
        xs = xs[:n_samples]
        ys = ys[:n_samples]

        if n_samples == 0:
            x_one, y_one, f_one = self._read_sample_once()
            xs = np.asarray([x_one], dtype=float)
            ys = np.asarray([y_one], dtype=float)
            freqs = np.asarray([f_one], dtype=float)
            n_samples = 1

        x_mean = float(np.mean(xs))
        y_mean = float(np.mean(ys))
        x_std = float(np.std(xs, ddof=1)) if n_samples > 1 else np.nan
        y_std = float(np.std(ys, ddof=1)) if n_samples > 1 else np.nan
        freq_mean = float(np.mean(freqs)) if len(freqs) else np.nan
        freq_std = float(np.std(freqs, ddof=1)) if len(freqs) > 1 else np.nan
        status = "ok" if n_samples >= self.min_samples else "few_samples"

        return {
            "step_index": int(step_index),
            "temperature": self._float_or_nan(target.get("temperature")),
            "left_right": str(target.get("left_right", "")),
            "f_drive": frequency,
            "peak_R": self._float_or_nan(target.get("peak_R")),
            "V_drive": voltage,
            "x_bkg": x_mean,
            "x_bkg_std": x_std,
            "y_bkg": y_mean,
            "y_bkg_std": y_std,
            "r_bkg": float(np.hypot(x_mean, y_mean)),
            "phase_bkg": float(np.degrees(np.arctan2(y_mean, x_mean))),
            "n_samples": int(n_samples),
            "sample_duration": float(self.sample_duration),
            "settle_time": float(self.settle_time),
            "frequency_readback": float(self._get_freq()),
            "frequency_measured_mean": freq_mean,
            "frequency_measured_std": freq_std,
            "voltage_readback": float(self._get_amp()),
            "demod_phase_readback": float(self._get_demod_phase()),
            "status": status,
            "target_label": str(target.get("target_label", "")),
            "utc_start": float(utc_start),
            "utc_end": float(utc_end),
        }

    def _poll_samples(self) -> Dict[str, Iterable]:
        samples = {"x": [], "y": [], "frequency": []}
        t_end = time.time() + float(self.sample_duration)

        self.daq.subscribe(self.sample_path)
        self.daq.sync()
        try:
            while time.time() < t_end and not self.should_stop():
                chunk = min(float(self.poll_interval), t_end - time.time())
                if chunk <= 0:
                    break
                timeout_ms = int(1000 * (chunk + 0.1))
                poll_data = self.daq.poll(
                    chunk, timeout_ms=timeout_ms, flags=0, flat=True
                )
                sample = self._extract_sample(poll_data)
                if not sample:
                    continue
                for key in samples:
                    values = np.asarray(sample.get(key, []), dtype=float)
                    if values.size:
                        samples[key].extend(values.tolist())
        finally:
            self.daq.unsubscribe(self.sample_path)
        return samples

    def _extract_sample(self, poll_data: Dict) -> Optional[Dict[str, Iterable]]:
        if self.sample_path in poll_data:
            return poll_data[self.sample_path]
        try:
            return poll_data[self.zur_id]["demods"][str(self.demod_index)]["sample"]
        except Exception:
            return None

    def _read_sample_once(self):
        resp = self.daq.getSample(self.sample_path)
        return resp["x"][0], resp["y"][0], resp["frequency"][0]

    def _get_amp(self) -> float:
        return self.daq.getDouble(f"/{self.zur_id}/sigouts/0/amplitudes/{self.osc_index}")

    def _get_freq(self) -> float:
        return self.daq.getDouble(f"/{self.zur_id}/oscs/{self.osc_index}/freq")

    def _get_demod_phase(self) -> float:
        return self.daq.getDouble(
            f"/{self.zur_id}/demods/{self.demod_index}/phaseshift"
        )

    def _set_drive(self, amplitude: float, frequency: float):
        self.daq.setDouble(
            f"/{self.zur_id}/sigouts/0/amplitudes/{self.osc_index}",
            float(amplitude),
        )
        self.daq.setDouble(
            f"/{self.zur_id}/oscs/{self.osc_index}/freq",
            float(frequency),
        )
        self.daq.sync()

    def _sleep_with_abort(self, duration: float) -> bool:
        end_time = time.time() + float(duration)
        while True:
            if self.should_stop():
                return False
            remaining = end_time - time.time()
            if remaining <= 0:
                return True
            try:
                time.sleep(min(0.1, remaining))
            except KeyboardInterrupt:
                return False

    @staticmethod
    def _float_or_nan(value) -> float:
        try:
            if pd.isna(value):
                return np.nan
            return float(value)
        except Exception:
            return np.nan

    @staticmethod
    def _is_blank(value) -> bool:
        if value is None:
            return True
        try:
            if pd.isna(value):
                return True
        except Exception:
            pass
        return str(value).strip() == ""


class OffResonanceBackgroundConsole(ManagedConsole):
    """ManagedConsole with a project default result directory."""

    def __init__(self, procedure_class):
        super().__init__(procedure_class=procedure_class)
        self.parameter_values = {
            key: value
            for key, value in self.parameter_values.items()
            if value is not None
        }
        if not self._result_dir_explicit() and self.directory in (None, "", "."):
            self.directory = str(DEFAULT_RESULT_DIR)
            DEFAULT_RESULT_DIR.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _result_dir_explicit():
        for arg in sys.argv[1:]:
            if arg == "--result-directory" or arg.startswith("--result-directory="):
                return True
        return False


def main():
    app = OffResonanceBackgroundConsole(
        procedure_class=OffResonanceBackgroundProcedure
    )
    try:
        sys.exit(app.exec())
    except KeyboardInterrupt:
        app.abort()
        sys.exit(1)


if __name__ == "__main__":
    main()
