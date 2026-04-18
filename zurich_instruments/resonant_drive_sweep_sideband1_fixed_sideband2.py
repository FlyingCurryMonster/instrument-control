import csv
import logging
import re
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import zhinst.core
from pymeasure.display.inputs import ScientificInput
from pymeasure.display.Qt import QtWidgets
from pymeasure.display.widgets import PlotWidget
from pymeasure.display.windows.managed_dock_window import ManagedDockWindow
from pymeasure.experiment import (
    BooleanParameter,
    FloatParameter,
    IntegerParameter,
    Parameter,
    Procedure,
    Results,
    unique_filename,
)

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


class HighPrecisionScientificInput(ScientificInput):
    def textFromValue(self, value):
        precision = max(1, getattr(self._parameter, "decimals", 15))
        string = f"{value:.{precision}g}".replace("e+", "e")
        string = re.sub(r"e(-?)0*(\d+)", r"e\1\2", string)
        return string


def calculate_Q_infer(x: float, y: float, k: float) -> float:
    return k * (x**2 + y**2) / x


def calculate_f0_infer(x: float, y: float, f_drive: float, k: float) -> float:
    q = calculate_Q_infer(x, y, k)
    return f_drive * (1 + y / (x * 2 * q))


class ResonantDriveSweepSideband1FixedSideband2Procedure(Procedure):
    """Sweep sideband 1 drive while retuning sideband 1 and holding sideband 2 fixed."""

    MAX_SAFE_DRIVE = 10e-3
    CARRIER_DEMOD_INDEX = 0
    SIDEBAND1_SLOT_INDEX = 0
    SIDEBAND2_SLOT_INDEX = 1

    sideband1_k = FloatParameter("Sideband 1 k constant", units="1/V", default=3931072.9988525705)
    sideband1_V0 = FloatParameter(
        "Sideband 1 drive that k was obtained at", units="V", default=300e-6
    )
    sideband2_k = FloatParameter("Sideband 2 k constant", units="1/V", default=93260373.7208108)
    sideband2_V0 = FloatParameter(
        "Sideband 2 drive that k was obtained at", units="V", default=267.6e-6
    )

    carrier_xbkg = FloatParameter(
        "Carrier X background before rotation", units="V", default=-2.16e-3
    )
    carrier_ybkg = FloatParameter(
        "Carrier Y background before rotation", units="V", default=0.7886e-3
    )
    sideband1_xbkg = FloatParameter(
        "Sideband 1 X background before rotation", units="V", default=-2.16e-3
    )
    sideband1_ybkg = FloatParameter(
        "Sideband 1 Y background before rotation", units="V", default=0.7886e-3
    )
    sideband2_xbkg = FloatParameter(
        "Sideband 2 X background before rotation", units="V", default=27.189e-6
    )
    sideband2_ybkg = FloatParameter(
        "Sideband 2 Y background before rotation", units="V", default=-12.67e-6
    )
    carrier_phase_rotation = FloatParameter(
        "Carrier phase rotation", units="deg", default=-112.87
    )
    sideband1_phase_rotation = FloatParameter(
        "Sideband 1 phase rotation", units="deg", default=-95.3
    )
    sideband2_phase_rotation = FloatParameter(
        "Sideband 2 phase rotation", units="deg", default=-98.367
    )

    carrier_drive = FloatParameter("Carrier drive", units="V", default=0.0)
    carrier_frequency = FloatParameter(
        "Carrier frequency",
        units="Hz",
        default=800.0,
        decimals=10,
        ui_class=HighPrecisionScientificInput,
    )
    sideband1_start_drive = FloatParameter("Sideband 1 start drive", units="V", default=0.1e-3)
    sideband1_end_drive = FloatParameter("Sideband 1 end drive", units="V", default=3e-3)
    num_points = IntegerParameter("Number of points", default=10)
    logspace = BooleanParameter("Log10 grid", default=True)
    use_drive_freq_csv = BooleanParameter("Drive/freq settings from csv", default=False)
    drive_freq_csv_path = Parameter("Drive/freq csv path", default="")

    sideband1_initial_target = FloatParameter(
        "Initial sideband 1 target fc-fm1",
        units="Hz",
        default=645.702,
        decimals=10,
        ui_class=HighPrecisionScientificInput,
    )
    sideband2_fixed_target = FloatParameter(
        "Fixed sideband 2 target fc+fm2",
        units="Hz",
        default=1320.93209,
        decimals=10,
        ui_class=HighPrecisionScientificInput,
    )
    sideband2_drive = FloatParameter("Sideband 2 drive", units="V", default=370.9e-6)

    reverse_sweep = BooleanParameter("Reverse sweep", default=True)
    retune_down_sweep = BooleanParameter("Retune to resonance on down sweep", default=True)
    phase_band = FloatParameter("Sideband 1 phase band", units="deg", default=6.0)
    max_iterations = IntegerParameter("Max retune iterations", default=5)
    fixed_delay_time = FloatParameter("Fixed delay time", units="s", default=1000.0)
    delay_mode = Parameter("Delay mode (fixed|max|tau)", default="fixed")

    file_prefix = Parameter("File prefix", default="resonant_drive_sweep_sideband1_fixed_sideband2")
    zur_id = Parameter("Zurich addr.", default="dev4934")
    carrier_osc_num = IntegerParameter("Carrier oscillator number", default=1)
    sideband1_osc_num = IntegerParameter("Sideband 1 oscillator number", default=2)
    sideband2_osc_num = IntegerParameter("Sideband 2 oscillator number", default=3)
    sideband1_demod_num = IntegerParameter("Sideband 1 demodulator number", default=2)
    sideband2_demod_num = IntegerParameter("Sideband 2 demodulator number", default=3)
    server_host = Parameter("Server host", default="192.168.77.26")
    server_port = IntegerParameter("Server port", default=8004)
    interface = Parameter("Interface", default="PCIe")
    comments = Parameter("Comments/Notes", default="")

    PARAMETERS = [
        "sideband1_k",
        "sideband1_V0",
        "sideband2_k",
        "sideband2_V0",
        "carrier_xbkg",
        "carrier_ybkg",
        "sideband1_xbkg",
        "sideband1_ybkg",
        "sideband2_xbkg",
        "sideband2_ybkg",
        "carrier_phase_rotation",
        "sideband1_phase_rotation",
        "sideband2_phase_rotation",
        "carrier_drive",
        "carrier_frequency",
        "sideband1_start_drive",
        "sideband1_end_drive",
        "num_points",
        "logspace",
        "use_drive_freq_csv",
        "drive_freq_csv_path",
        "sideband1_initial_target",
        "sideband2_fixed_target",
        "sideband2_drive",
        "reverse_sweep",
        "retune_down_sweep",
        "phase_band",
        "max_iterations",
        "fixed_delay_time",
        "delay_mode",
        "file_prefix",
        "zur_id",
        "carrier_osc_num",
        "sideband1_osc_num",
        "sideband2_osc_num",
        "sideband1_demod_num",
        "sideband2_demod_num",
        "server_host",
        "server_port",
        "interface",
        "comments",
    ]

    DATA_COLUMNS = [
        "step_index",
        "drive_index",
        "sweep_direction",
        "utc",
        "carrier_drive_set",
        "carrier_drive_readback",
        "sideband1_drive_set",
        "sideband1_drive_readback",
        "sideband2_drive_set",
        "sideband2_drive_readback",
        "f_carrier_set",
        "f_carrier_readback",
        "f_sideband1_target_set",
        "f_sideband1_target_readback",
        "f_sideband1_osc_set",
        "f_sideband1_osc_readback",
        "f_sideband2_target_set",
        "f_sideband2_target_readback",
        "f_sideband2_osc_set",
        "f_sideband2_osc_readback",
        "carrier_demod_freq",
        "sideband1_demod_freq",
        "sideband2_demod_freq",
        "carrier_X",
        "carrier_Y",
        "carrier_R",
        "carrier_phase",
        "sideband1_X",
        "sideband1_Y",
        "sideband1_R",
        "sideband1_phase",
        "sideband2_X",
        "sideband2_Y",
        "sideband2_R",
        "sideband2_phase",
        "sideband1_Q_infer",
        "sideband1_f0_infer",
        "sideband1_tau_infer",
        "sideband2_Q_infer",
        "sideband2_f0_infer",
        "sideband2_tau_infer",
        "in_band",
        "iterations",
        "retuned",
        "delay_used",
    ]

    def startup(self):
        log.info("Starting resonant sideband 1 drive sweep with fixed sideband 2")
        self._validate_parameters()

        self.carrier_osc_index = self.carrier_osc_num - 1
        self.sideband1_osc_index = self.sideband1_osc_num - 1
        self.sideband2_osc_index = self.sideband2_osc_num - 1
        self.sideband1_demod_index = self.sideband1_demod_num - 1
        self.sideband2_demod_index = self.sideband2_demod_num - 1

        self.daq = zhinst.core.ziDAQServer(
            self.server_host, int(self.server_port), api_level=6
        )
        self.daq.connectDevice(self.zur_id, interface=self.interface)

        self.carrier_sample_path = f"/{self.zur_id}/demods/{self.CARRIER_DEMOD_INDEX}/sample"
        self.sideband1_sample_path = (
            f"/{self.zur_id}/demods/{self.sideband1_demod_index}/sample"
        )
        self.sideband2_sample_path = (
            f"/{self.zur_id}/demods/{self.sideband2_demod_index}/sample"
        )
        self.daq.set(f"/{self.zur_id}/demods/{self.CARRIER_DEMOD_INDEX}/enable", 1)
        self.daq.set(f"/{self.zur_id}/demods/{self.sideband1_demod_index}/enable", 1)
        self.daq.set(f"/{self.zur_id}/demods/{self.sideband2_demod_index}/enable", 1)

        self.restore_carrier_amp = self._get_carrier_amp()
        self.restore_sideband1_amp = self._get_sideband_amp(self.SIDEBAND1_SLOT_INDEX)
        self.restore_sideband2_amp = self._get_sideband_amp(self.SIDEBAND2_SLOT_INDEX)
        self.restore_carrier_freq = self._get_freq(self.carrier_osc_index)
        self.restore_sideband1_freq = self._get_freq(self.sideband1_osc_index)
        self.restore_sideband2_freq = self._get_freq(self.sideband2_osc_index)
        self._validate_restore_state()
        self._log_state(
            "Startup restore state",
            self.restore_carrier_amp,
            self.restore_sideband1_amp,
            self.restore_sideband2_amp,
            self.restore_carrier_freq,
            self.restore_sideband1_freq,
            self.restore_sideband2_freq,
        )

        self.delay_mode = self._normalize_delay_mode(self.delay_mode)
        if self.use_drive_freq_csv:
            self.drive_points, self.start_targets = self._load_drive_freq_csv()
        else:
            self.drive_points = self._build_drive_points()
            self.start_targets = None

        self.up_sweep_targets: List[Optional[float]] = [None] * len(self.drive_points)
        self.step_index = 0
        self.last_tau: Optional[float] = None

        self._set_fixed_channels()

    def execute(self):
        current_target = float(self.sideband1_initial_target)
        current_target = self._run_sweep(
            indices=range(len(self.drive_points)),
            direction=1,
            retune=True,
            use_up_targets=False,
            current_target=current_target,
            start_targets=self.start_targets,
        )

        if self.reverse_sweep and not self.should_stop():
            self._run_sweep(
                indices=range(len(self.drive_points) - 1, -1, -1),
                direction=-1,
                retune=self.retune_down_sweep,
                use_up_targets=True,
                current_target=current_target,
                start_targets=self.start_targets,
            )

    def shutdown(self):
        if not hasattr(self, "daq"):
            return
        try:
            self._restore_state()
        except Exception:
            log.exception("Unable to restore original drive and frequency settings")

    def _run_sweep(
        self,
        indices,
        direction: int,
        retune: bool,
        use_up_targets: bool,
        current_target: float,
        start_targets: Optional[List[float]],
    ) -> float:
        for idx in indices:
            if self.should_stop():
                log.warning("Stop requested before drive index %d", idx)
                break

            drive = float(self.drive_points[idx])
            if use_up_targets and self.up_sweep_targets[idx] is not None:
                start_target = float(self.up_sweep_targets[idx])
            elif start_targets is not None:
                start_target = float(start_targets[idx])
            else:
                start_target = float(current_target)

            final_target, completed = self._tune_and_measure(
                drive=drive,
                start_target=start_target,
                retune=retune,
                drive_index=idx,
                sweep_direction=direction,
            )
            current_target = final_target
            if direction > 0:
                self.up_sweep_targets[idx] = final_target
            if not completed:
                break
        return current_target

    def _tune_and_measure(
        self,
        drive: float,
        start_target: float,
        retune: bool,
        drive_index: int,
        sweep_direction: int,
    ) -> Tuple[float, bool]:
        current_target = float(start_target)
        iterations = 0
        retuned = False
        last_delay = 0.0

        while True:
            if self.should_stop():
                return current_target, False

            self._set_sideband1_drive(drive, current_target)
            last_delay = self._delay_after_set(self.last_tau)
            if not self._sleep_with_abort(last_delay):
                return current_target, False

            measurement = self._measure_once()
            iterations += 1

            in_band = abs(measurement["sideband1_phase"]) <= self.phase_band
            self._update_last_tau(measurement)
            measurement.update(
                {
                    "step_index": int(self.step_index),
                    "drive_index": int(drive_index),
                    "sweep_direction": int(sweep_direction),
                    "carrier_drive_set": float(self.carrier_drive),
                    "sideband1_drive_set": float(drive),
                    "sideband2_drive_set": float(self.sideband2_drive),
                    "f_carrier_set": float(self.carrier_frequency),
                    "f_sideband1_target_set": float(current_target),
                    "f_sideband1_osc_set": float(
                        self._sideband1_osc_freq_for_target(current_target)
                    ),
                    "f_sideband2_target_set": float(self.sideband2_fixed_target),
                    "f_sideband2_osc_set": float(self._sideband2_osc_freq()),
                    "in_band": int(in_band),
                    "iterations": int(iterations),
                    "retuned": int(retuned),
                    "delay_used": float(last_delay),
                }
            )
            self.emit("results", measurement)
            self.step_index += 1

            if in_band or not retune or iterations >= self.max_iterations:
                return current_target, True

            f0_infer = measurement["sideband1_f0_infer"]
            if not np.isfinite(f0_infer):
                return current_target, True

            current_target = float(f0_infer)
            retuned = True

    def _set_fixed_channels(self):
        self._validate_safe_amplitude("carrier_drive", self.carrier_drive)
        self._validate_safe_amplitude("sideband2_drive", self.sideband2_drive)
        self.daq.setDouble(self._carrier_amp_path(), float(self.carrier_drive))
        self.daq.setDouble(self._freq_path(self.carrier_osc_index), float(self.carrier_frequency))
        self.daq.setDouble(
            self._sideband_amp_path(self.SIDEBAND2_SLOT_INDEX),
            float(self.sideband2_drive),
        )
        self.daq.setDouble(self._freq_path(self.sideband2_osc_index), float(self._sideband2_osc_freq()))
        self.daq.sync()

    def _set_sideband1_drive(self, amplitude: float, target_frequency: float):
        self._validate_safe_amplitude("sideband1_drive", amplitude)
        self.daq.setDouble(
            self._sideband_amp_path(self.SIDEBAND1_SLOT_INDEX),
            float(amplitude),
        )
        self.daq.setDouble(
            self._freq_path(self.sideband1_osc_index),
            float(self._sideband1_osc_freq_for_target(target_frequency)),
        )
        self.daq.sync()

    def _measure_once(self) -> dict:
        carrier_x_raw, carrier_y_raw, carrier_demod_freq = self._sample_read(
            self.carrier_sample_path
        )
        sideband1_x_raw, sideband1_y_raw, sideband1_demod_freq = self._sample_read(
            self.sideband1_sample_path
        )
        sideband2_x_raw, sideband2_y_raw, sideband2_demod_freq = self._sample_read(
            self.sideband2_sample_path
        )

        carrier_x, carrier_y = self._subtract_background_and_rotate(
            carrier_x_raw,
            carrier_y_raw,
            self.carrier_xbkg,
            self.carrier_ybkg,
            self.carrier_phase_rotation,
        )
        sideband1_x, sideband1_y = self._subtract_background_and_rotate(
            sideband1_x_raw,
            sideband1_y_raw,
            self.sideband1_xbkg,
            self.sideband1_ybkg,
            self.sideband1_phase_rotation,
        )
        sideband2_x, sideband2_y = self._subtract_background_and_rotate(
            sideband2_x_raw,
            sideband2_y_raw,
            self.sideband2_xbkg,
            self.sideband2_ybkg,
            self.sideband2_phase_rotation,
        )

        carrier_drive_readback = self._get_carrier_amp()
        sideband1_drive_readback = self._get_sideband_amp(self.SIDEBAND1_SLOT_INDEX)
        sideband2_drive_readback = self._get_sideband_amp(self.SIDEBAND2_SLOT_INDEX)
        carrier_freq_readback = self._get_freq(self.carrier_osc_index)
        sideband1_freq_readback = self._get_freq(self.sideband1_osc_index)
        sideband2_freq_readback = self._get_freq(self.sideband2_osc_index)
        sideband1_target_readback = carrier_freq_readback - sideband1_freq_readback
        sideband2_target_readback = carrier_freq_readback + sideband2_freq_readback

        sideband1_q, sideband1_f0, sideband1_tau = self._infer_metrics(
            sideband1_x,
            sideband1_y,
            sideband1_target_readback,
            sideband1_drive_readback,
            self.sideband1_k,
            self.sideband1_V0,
        )
        sideband2_q, sideband2_f0, sideband2_tau = self._infer_metrics(
            sideband2_x,
            sideband2_y,
            sideband2_target_readback,
            sideband2_drive_readback,
            self.sideband2_k,
            self.sideband2_V0,
        )

        return {
            "utc": time.time(),
            "carrier_drive_readback": float(carrier_drive_readback),
            "sideband1_drive_readback": float(sideband1_drive_readback),
            "sideband2_drive_readback": float(sideband2_drive_readback),
            "f_carrier_readback": float(carrier_freq_readback),
            "f_sideband1_target_readback": float(sideband1_target_readback),
            "f_sideband1_osc_readback": float(sideband1_freq_readback),
            "f_sideband2_target_readback": float(sideband2_target_readback),
            "f_sideband2_osc_readback": float(sideband2_freq_readback),
            "carrier_demod_freq": float(carrier_demod_freq),
            "sideband1_demod_freq": float(sideband1_demod_freq),
            "sideband2_demod_freq": float(sideband2_demod_freq),
            "carrier_X": float(carrier_x),
            "carrier_Y": float(carrier_y),
            "carrier_R": float(np.hypot(carrier_x, carrier_y)),
            "carrier_phase": float(np.degrees(np.arctan2(carrier_y, carrier_x))),
            "sideband1_X": float(sideband1_x),
            "sideband1_Y": float(sideband1_y),
            "sideband1_R": float(np.hypot(sideband1_x, sideband1_y)),
            "sideband1_phase": float(np.degrees(np.arctan2(sideband1_y, sideband1_x))),
            "sideband2_X": float(sideband2_x),
            "sideband2_Y": float(sideband2_y),
            "sideband2_R": float(np.hypot(sideband2_x, sideband2_y)),
            "sideband2_phase": float(np.degrees(np.arctan2(sideband2_y, sideband2_x))),
            "sideband1_Q_infer": float(sideband1_q),
            "sideband1_f0_infer": float(sideband1_f0),
            "sideband1_tau_infer": float(sideband1_tau),
            "sideband2_Q_infer": float(sideband2_q),
            "sideband2_f0_infer": float(sideband2_f0),
            "sideband2_tau_infer": float(sideband2_tau),
        }

    def _infer_metrics(
        self,
        x: float,
        y: float,
        frequency: float,
        drive_readback: float,
        k: float,
        v0: float,
    ) -> Tuple[float, float, float]:
        q_infer = np.nan
        f0_infer = np.nan
        tau_infer = np.nan
        if drive_readback > 0 and x != 0:
            k_effective = float(k) * float(v0) / float(drive_readback)
            q_infer = calculate_Q_infer(x, y, k_effective)
            if np.isfinite(q_infer) and q_infer != 0:
                f0_infer = calculate_f0_infer(x, y, frequency, k_effective)
                if np.isfinite(f0_infer) and f0_infer != 0:
                    tau_infer = q_infer / (np.pi * f0_infer)
        return float(q_infer), float(f0_infer), float(tau_infer)

    def _update_last_tau(self, measurement: dict) -> None:
        tau = measurement.get("sideband1_tau_infer")
        if tau is not None and np.isfinite(tau) and tau > 0:
            self.last_tau = float(tau)

    def _delay_after_set(self, last_tau: Optional[float]) -> float:
        if self.delay_mode == "fixed":
            return max(0.0, float(self.fixed_delay_time))
        if last_tau is None or not np.isfinite(last_tau):
            return max(0.0, float(self.fixed_delay_time))
        tau_delay = 5.0 * float(last_tau)
        if self.delay_mode == "max":
            return max(float(self.fixed_delay_time), tau_delay)
        if self.delay_mode == "tau":
            return tau_delay
        return max(0.0, float(self.fixed_delay_time))

    def _sleep_with_abort(self, duration: float) -> bool:
        if duration <= 0:
            return True
        end_time = time.time() + duration
        while True:
            if self.should_stop():
                return False
            remaining = end_time - time.time()
            if remaining <= 0:
                return True
            time.sleep(min(0.1, remaining))

    def _build_drive_points(self) -> np.ndarray:
        if self.logspace:
            return np.logspace(
                np.log10(self.sideband1_start_drive),
                np.log10(self.sideband1_end_drive),
                int(self.num_points),
            )
        return np.linspace(
            self.sideband1_start_drive,
            self.sideband1_end_drive,
            int(self.num_points),
        )

    def _load_drive_freq_csv(self) -> Tuple[List[float], List[float]]:
        path = Path(str(self.drive_freq_csv_path)).expanduser()
        if not path.exists():
            raise ValueError(f"CSV file not found: {path}")
        drives: List[float] = []
        targets: List[float] = []
        with path.open("r", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=",")
            if reader.fieldnames is None:
                raise ValueError("CSV must include headers: drive_voltage, sideband1_target.")
            required = {"drive_voltage", "sideband1_target"}
            if not required.issubset(set(reader.fieldnames)):
                raise ValueError("CSV headers must include drive_voltage and sideband1_target.")
            for row in reader:
                drive = float(str(row.get("drive_voltage", "")).strip())
                target = float(str(row.get("sideband1_target", "")).strip())
                self._validate_safe_amplitude("csv drive_voltage", drive)
                self._sideband1_osc_freq_for_target(target)
                drives.append(drive)
                targets.append(target)
        if not drives:
            raise ValueError("CSV contains no drive/frequency rows.")
        return drives, targets

    @staticmethod
    def _normalize_delay_mode(mode: object) -> str:
        text = str(mode).strip().lower()
        if text in ("fixed", "fixed_delay"):
            return "fixed"
        if text in ("max", "max_fixed", "max(fixed,5tau)"):
            return "max"
        if text in ("tau", "tau_only", "5tau"):
            return "tau"
        return text

    def _validate_parameters(self) -> None:
        if self.num_points <= 0:
            raise ValueError("Number of points must be >= 1.")
        if not self.use_drive_freq_csv:
            self._validate_safe_amplitude("sideband1_start_drive", self.sideband1_start_drive)
            self._validate_safe_amplitude("sideband1_end_drive", self.sideband1_end_drive)
            if self.logspace and (
                self.sideband1_start_drive <= 0 or self.sideband1_end_drive <= 0
            ):
                raise ValueError("Logspace sweep requires positive start/end drive.")
        if self.max_iterations <= 0:
            raise ValueError("Max retune iterations must be >= 1.")
        if self.phase_band < 0:
            raise ValueError("Phase band must be >= 0.")
        if self.fixed_delay_time < 0:
            raise ValueError("Fixed delay time must be >= 0.")
        if self.carrier_frequency <= 0:
            raise ValueError("Carrier frequency must be > 0.")
        if self.sideband1_initial_target <= 0:
            raise ValueError("Initial sideband 1 target must be > 0.")
        if self.sideband2_fixed_target <= 0:
            raise ValueError("Fixed sideband 2 target must be > 0.")
        if min(
            self.carrier_osc_num,
            self.sideband1_osc_num,
            self.sideband2_osc_num,
            self.sideband1_demod_num,
            self.sideband2_demod_num,
        ) <= 0:
            raise ValueError("Oscillator and demodulator numbers are 1-based and must be > 0.")
        if self.sideband1_demod_num == 1 or self.sideband2_demod_num == 1:
            raise ValueError("Carrier is fixed to demodulator 1 in the UI; sideband demodulators must be > 1.")
        self._validate_safe_amplitude("carrier_drive", self.carrier_drive)
        self._validate_safe_amplitude("sideband2_drive", self.sideband2_drive)
        self._sideband1_osc_freq_for_target(self.sideband1_initial_target)
        self._sideband2_osc_freq()
        mode = self._normalize_delay_mode(self.delay_mode)
        if mode not in ("fixed", "max", "tau"):
            raise ValueError("Delay mode must be one of: fixed, max, tau.")
        if self.use_drive_freq_csv and not str(self.drive_freq_csv_path).strip():
            raise ValueError("CSV path must be set when drive/freq settings from csv is enabled.")

    def _sideband1_osc_freq_for_target(self, target_frequency: float) -> float:
        osc_freq = float(self.carrier_frequency) - float(target_frequency)
        if osc_freq < 0:
            raise ValueError(
                "Sideband 1 target %.10f Hz is above carrier %.10f Hz, producing negative fm1."
                % (target_frequency, self.carrier_frequency)
            )
        return osc_freq

    def _sideband2_osc_freq(self) -> float:
        osc_freq = float(self.sideband2_fixed_target) - float(self.carrier_frequency)
        if osc_freq < 0:
            raise ValueError(
                "Fixed sideband 2 target %.10f Hz is below carrier %.10f Hz, producing negative fm2."
                % (self.sideband2_fixed_target, self.carrier_frequency)
            )
        return osc_freq

    def _restore_state(self) -> None:
        self._log_state(
            "Requested restore state",
            self.restore_carrier_amp,
            self.restore_sideband1_amp,
            self.restore_sideband2_amp,
            self.restore_carrier_freq,
            self.restore_sideband1_freq,
            self.restore_sideband2_freq,
        )
        if self._is_safe_amplitude(self.restore_carrier_amp):
            self.daq.setDouble(self._carrier_amp_path(), float(self.restore_carrier_amp))
        else:
            log.error("Skipping unsafe carrier restore amplitude %.9g V", self.restore_carrier_amp)
        if self._is_safe_amplitude(self.restore_sideband1_amp):
            self.daq.setDouble(
                self._sideband_amp_path(self.SIDEBAND1_SLOT_INDEX),
                float(self.restore_sideband1_amp),
            )
        else:
            log.error("Skipping unsafe sideband 1 restore amplitude %.9g V", self.restore_sideband1_amp)
        if self._is_safe_amplitude(self.restore_sideband2_amp):
            self.daq.setDouble(
                self._sideband_amp_path(self.SIDEBAND2_SLOT_INDEX),
                float(self.restore_sideband2_amp),
            )
        else:
            log.error("Skipping unsafe sideband 2 restore amplitude %.9g V", self.restore_sideband2_amp)
        self.daq.setDouble(self._freq_path(self.carrier_osc_index), float(self.restore_carrier_freq))
        self.daq.setDouble(self._freq_path(self.sideband1_osc_index), float(self.restore_sideband1_freq))
        self.daq.setDouble(self._freq_path(self.sideband2_osc_index), float(self.restore_sideband2_freq))
        self.daq.sync()

    def _validate_restore_state(self) -> None:
        self._validate_safe_amplitude("restore_carrier_amp", self.restore_carrier_amp)
        self._validate_safe_amplitude("restore_sideband1_amp", self.restore_sideband1_amp)
        self._validate_safe_amplitude("restore_sideband2_amp", self.restore_sideband2_amp)

    def _carrier_amp_path(self) -> str:
        return f"/{self.zur_id}/mods/0/carrier/amplitude"

    def _sideband_amp_path(self, slot_index: int) -> str:
        return f"/{self.zur_id}/mods/0/sidebands/{slot_index}/amplitude"

    def _freq_path(self, osc_index: int) -> str:
        return f"/{self.zur_id}/oscs/{osc_index}/freq"

    def _get_carrier_amp(self) -> float:
        return self.daq.getDouble(self._carrier_amp_path())

    def _get_sideband_amp(self, slot_index: int) -> float:
        return self.daq.getDouble(self._sideband_amp_path(slot_index))

    def _get_freq(self, osc_index: int) -> float:
        return self.daq.getDouble(self._freq_path(osc_index))

    def _validate_safe_amplitude(self, label: str, amplitude: float) -> None:
        if not self._is_safe_amplitude(amplitude):
            raise ValueError(
                f"{label}={float(amplitude):.9g} V exceeds the hard safety limit of "
                f"{self.MAX_SAFE_DRIVE:.9g} V."
            )

    def _is_safe_amplitude(self, amplitude: float) -> bool:
        value = float(amplitude)
        return 0 <= value <= self.MAX_SAFE_DRIVE

    def _subtract_background_and_rotate(
        self,
        x_raw: float,
        y_raw: float,
        x_bkg: float,
        y_bkg: float,
        phase_rotation_deg: float,
    ) -> Tuple[float, float]:
        x = float(x_raw) - float(x_bkg)
        y = float(y_raw) - float(y_bkg)
        theta = np.radians(float(phase_rotation_deg))
        cos_theta = np.cos(theta)
        sin_theta = np.sin(theta)
        x_rot = cos_theta * x - sin_theta * y
        y_rot = sin_theta * x + cos_theta * y
        return float(x_rot), float(y_rot)

    def _sample_read(self, sample_path: str):
        resp = self.daq.getSample(sample_path)
        return resp["x"][0], resp["y"][0], resp["frequency"][0]

    def _log_state(
        self,
        label: str,
        carrier_amp: float,
        sideband1_amp: float,
        sideband2_amp: float,
        carrier_freq: float,
        sideband1_freq: float,
        sideband2_freq: float,
    ) -> None:
        log.info(
            "%s: carrier_amp=%.9g V sideband1_amp=%.9g V sideband2_amp=%.9g V "
            "carrier_freq=%.10f Hz sideband1_freq=%.10f Hz sideband2_freq=%.10f Hz",
            label,
            carrier_amp,
            sideband1_amp,
            sideband2_amp,
            carrier_freq,
            sideband1_freq,
            sideband2_freq,
        )


class ResonantDriveSweepSideband1FixedSideband2Window(ManagedDockWindow):
    def __init__(self):
        drive_plot = PlotWidget(
            name="Sideband 1 Drive Sweep",
            columns=ResonantDriveSweepSideband1FixedSideband2Procedure.DATA_COLUMNS,
            x_axis="sideband1_drive_set",
            y_axis="sideband1_f0_infer",
        )
        phase_plot = PlotWidget(
            name="Sideband 1 Phase",
            columns=ResonantDriveSweepSideband1FixedSideband2Procedure.DATA_COLUMNS,
            x_axis="sideband1_drive_set",
            y_axis="sideband1_phase",
        )
        q_plot = PlotWidget(
            name="Sideband 1 Q",
            columns=ResonantDriveSweepSideband1FixedSideband2Procedure.DATA_COLUMNS,
            x_axis="sideband1_drive_set",
            y_axis="sideband1_Q_infer",
        )
        sideband2_plot = PlotWidget(
            name="Sideband 2 Diagnostics",
            columns=ResonantDriveSweepSideband1FixedSideband2Procedure.DATA_COLUMNS,
            x_axis="sideband1_drive_set",
            y_axis="sideband2_R",
        )

        super().__init__(
            procedure_class=ResonantDriveSweepSideband1FixedSideband2Procedure,
            inputs=ResonantDriveSweepSideband1FixedSideband2Procedure.PARAMETERS,
            displays=ResonantDriveSweepSideband1FixedSideband2Procedure.PARAMETERS,
            x_axis=["sideband1_drive_set"],
            y_axis=[
                "sideband1_f0_infer",
                "sideband1_phase",
                "sideband1_Q_infer",
                "sideband2_R",
            ],
            widget_list=(drive_plot, phase_plot, q_plot, sideband2_plot),
            inputs_in_scrollarea=True,
        )
        self.setWindowTitle("Zurich Resonant Drive Sweep Sideband1 Fixed Sideband2")
        self.directory = r"D:/Data/Fall25-Summer26/TO freq-sweeps"

    def queue(self):
        directory = self.directory
        procedure = self.make_procedure()
        prefix = f"{procedure.file_prefix}_" if procedure.file_prefix else ""
        filename = unique_filename(directory, prefix=prefix)
        results = Results(procedure, filename)
        experiment = self.new_experiment(results)
        self.manager.queue(experiment)


if __name__ == "__main__":
    app = QtWidgets.QApplication([])
    window = ResonantDriveSweepSideband1FixedSideband2Window()
    window.show()
    sys.exit(app.exec_())
