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


class ResonantDriveSweepSidebandProcedure(Procedure):
    """Sweep carrier drive amplitude while retuning on demod 1 and logging a sideband pickup."""

    carrier_k = FloatParameter("Carrier k constant", units="1/V", default=3931072.9988525705)
    carrier_V0 = FloatParameter(
        "Carrier drive that k was obtained at", units="V", default=300e-6
    )
    sideband_k = FloatParameter("Sideband k constant", units="1/V", default=93260373.7208108)
    sideband_V0 = FloatParameter(
        "Sideband drive that k was obtained at", units="V", default=267.6e-6
    )
    carrier_xbkg = FloatParameter("Carrier X background before rotation", units="V", default=-2.968e-3)
    carrier_ybkg = FloatParameter("Carrier Y background before rotation", units="V", default=1.8547e-3)
    sideband_xbkg = FloatParameter("Sideband X background before rotation", units="V", default=27.189e-6)
    sideband_ybkg = FloatParameter("Sideband Y background before rotation", units="V", default=-12.67e-6
                                   )
    carrier_phase_rotation = FloatParameter(
        "Carrier phase rotation", units="deg", default=-112.87)
    sideband_phase_rotation = FloatParameter(
        "Sideband phase rotation", units="deg", default=-98.367)

    start_drive = FloatParameter("Start drive", units="V", default=0.1e-3)
    end_drive = FloatParameter("End drive", units="V", default=3e-3)
    num_points = IntegerParameter("Number of points", default=10)
    logspace = BooleanParameter("Log10 grid", default=True)
    use_drive_freq_csv = BooleanParameter("Drive settings from csv", default=False)
    drive_freq_csv_path = Parameter("Drive/freq csv path", default="")

    reverse_sweep = BooleanParameter("Reverse sweep", default=True)
    retune_down_sweep = BooleanParameter("Retune to resonance on down sweep", default=True)

    phase_band = FloatParameter("Phase band", units="deg", default=6.0)
    max_iterations = IntegerParameter("Max retune iterations", default=5)

    use_current_frequency = BooleanParameter("Use current frequency", default=True)
    initial_frequency = FloatParameter(
        "Initial frequency",
        units="Hz",
        default=0.0,
        decimals=10,
        ui_class=HighPrecisionScientificInput,
    )

    fixed_delay_time = FloatParameter("Fixed delay time", units="s", default=1000)
    delay_mode = Parameter("Delay mode (fixed|max|tau)", default="fixed")

    file_prefix = Parameter("File prefix", default="resonant_drive_sweep_sideband")

    zur_id = Parameter("Zurich addr.", default="dev4934")
    carrier_osc_num = IntegerParameter("Carrier oscillator number", default=1)
    carrier_demod_num = IntegerParameter("Carrier demodulator number", default=1)
    sideband_osc_num = IntegerParameter("Sideband oscillator number", default=3)
    sideband_demod_num = IntegerParameter("Sideband demodulator number", default=3)
    fixed_sideband_demod_freq = FloatParameter(
        "Fixed sideband demod frequency",
        units="Hz",
        default=1320.937981,
        decimals=10,
        ui_class=HighPrecisionScientificInput,
    )
    server_host = Parameter("Server host", default="192.168.77.26")
    server_port = IntegerParameter("Server port", default=8004)
    interface = Parameter("Interface", default="PCIe")
    comments = Parameter("Comments/Notes", default="")

    PARAMETERS = [
        "fixed_sideband_demod_freq",
        "carrier_k",
        "carrier_V0",
        "sideband_k",
        "sideband_V0",
        "carrier_xbkg",
        "carrier_ybkg",
        "sideband_xbkg",
        "sideband_ybkg",
        "carrier_phase_rotation",
        "sideband_phase_rotation",
        "start_drive",
        "end_drive",
        "num_points",
        "logspace",
        "use_drive_freq_csv",
        "drive_freq_csv_path",
        "reverse_sweep",
        "retune_down_sweep",
        "phase_band",
        "max_iterations",
        "use_current_frequency",
        "initial_frequency",
        "fixed_delay_time",
        "delay_mode",
        "file_prefix",
        "zur_id",
        "carrier_osc_num",
        "carrier_demod_num",
        "sideband_osc_num",
        "sideband_demod_num",
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
        "sideband_drive_readback",
        "f_carrier_set",
        "f_carrier_readback",
        "f_sideband_osc_set",
        "f_sideband_osc_readback",
        "f_sideband_target",
        "f_sideband_sum_readback",
        "carrier_Q_infer",
        "carrier_f0_infer",
        "carrier_tau_infer",
        "sideband_Q_infer",
        "sideband_f0_infer",
        "sideband_tau_infer",
        "carrier_X",
        "carrier_Y",
        "carrier_R",
        "carrier_phase",
        "carrier_demod_freq",
        "sideband_demod_freq",
        "sideband_X",
        "sideband_Y",
        "sideband_R",
        "sideband_phase",
        "in_band",
        "iterations",
        "retuned",
        "delay_used",
    ]

    def startup(self):
        log.info("Starting resonant drive sweep with sideband pickup")
        self._validate_parameters()

        self.carrier_osc_index = self.carrier_osc_num - 1
        self.carrier_demod_index = self.carrier_demod_num - 1
        self.sideband_osc_index = self.sideband_osc_num - 1
        self.sideband_demod_index = self.sideband_demod_num - 1

        self.daq = zhinst.core.ziDAQServer(
            self.server_host, int(self.server_port), api_level=6
        )
        self.daq.connectDevice(self.zur_id, interface=self.interface)
        self.carrier_sample_path = f"/{self.zur_id}/demods/{self.carrier_demod_index}/sample"
        self.sideband_sample_path = (
            f"/{self.zur_id}/demods/{self.sideband_demod_index}/sample"
        )
        self.daq.set(f"/{self.zur_id}/demods/{self.carrier_demod_index}/enable", 1)
        self.daq.set(f"/{self.zur_id}/demods/{self.sideband_demod_index}/enable", 1)

        self.restore_amp = self._zurich_get_carrier_amp()
        self.restore_carrier_freq = self._zurich_get_freq(self.carrier_osc_index)
        self.restore_sideband_freq = self._zurich_get_freq(self.sideband_osc_index)

        self.delay_mode = self._normalize_delay_mode(self.delay_mode)
        if self.use_drive_freq_csv:
            self.drive_points, self.start_freqs = self._load_drive_freq_csv()
        else:
            self.drive_points = self._build_drive_points()
            self.start_freqs = None
        self.up_sweep_freqs: List[Optional[float]] = [None] * len(self.drive_points)
        self.step_index = 0
        self.last_tau: Optional[float] = None

    def execute(self):
        if self.use_drive_freq_csv:
            current_freq = self._zurich_get_freq(self.carrier_osc_index)
        elif self.use_current_frequency:
            current_freq = self._zurich_get_freq(self.carrier_osc_index)
        else:
            current_freq = float(self.initial_frequency)

        current_freq = self._run_sweep(
            indices=range(len(self.drive_points)),
            direction=1,
            retune=True,
            use_up_freqs=False,
            current_freq=current_freq,
            start_freqs=self.start_freqs,
        )

        if self.reverse_sweep and not self.should_stop():
            self._run_sweep(
                indices=range(len(self.drive_points) - 1, -1, -1),
                direction=-1,
                retune=self.retune_down_sweep,
                use_up_freqs=True,
                current_freq=current_freq,
                start_freqs=self.start_freqs,
            )

    def shutdown(self):
        if getattr(self, "restore_amp", None) is None:
            return
        try:
            self._restore_drive_state()
            log.info("Restored original drive and sideband settings")
        except Exception:
            log.exception("Unable to restore original drive and sideband settings")

    def _run_sweep(
        self,
        indices,
        direction: int,
        retune: bool,
        use_up_freqs: bool,
        current_freq: float,
        start_freqs: Optional[List[float]],
    ) -> float:
        for idx in indices:
            if self.should_stop():
                log.warning("Stop requested before drive index %d", idx)
                break

            drive = float(self.drive_points[idx])
            if use_up_freqs and self.up_sweep_freqs[idx] is not None:
                start_freq = float(self.up_sweep_freqs[idx])
            elif start_freqs is not None:
                start_freq = float(start_freqs[idx])
            else:
                start_freq = float(current_freq)

            final_freq, completed = self._tune_and_measure(
                drive=drive,
                start_freq=start_freq,
                retune=retune,
                drive_index=idx,
                sweep_direction=direction,
            )
            current_freq = final_freq
            if direction > 0:
                self.up_sweep_freqs[idx] = final_freq
            if not completed:
                break
        return current_freq

    def _tune_and_measure(
        self,
        drive: float,
        start_freq: float,
        retune: bool,
        drive_index: int,
        sweep_direction: int,
    ) -> Tuple[float, bool]:
        current_freq = float(start_freq)
        iterations = 0
        retuned = False
        last_delay = 0.0

        while True:
            if self.should_stop():
                return current_freq, False

            self._set_drive_and_sideband(drive, current_freq)
            last_delay = self._delay_after_set(self.last_tau)
            if not self._sleep_with_abort(last_delay):
                return current_freq, False

            measurement = self._measure_once()
            iterations += 1

            in_band = abs(measurement["carrier_phase"]) <= self.phase_band
            self._update_last_tau(measurement)

            measurement.update(
                {
                    "step_index": int(self.step_index),
                    "drive_index": int(drive_index),
                    "sweep_direction": int(sweep_direction),
                    "carrier_drive_set": float(drive),
                    "f_carrier_set": float(current_freq),
                    "in_band": int(in_band),
                    "iterations": int(iterations),
                    "retuned": int(retuned),
                    "delay_used": float(last_delay),
                }
            )
            self.emit("results", measurement)
            self.step_index += 1

            if in_band or not retune or iterations >= self.max_iterations:
                return current_freq, True

            f0_infer = measurement["carrier_f0_infer"]
            if not np.isfinite(f0_infer):
                return current_freq, True

            current_freq = float(f0_infer)
            retuned = True

    def _measure_once(self) -> dict:
        carrier_x_raw, carrier_y_raw, carrier_demod_freq = self._zurich_sample_read(
            self.carrier_sample_path
        )
        sideband_x_raw, sideband_y_raw, sideband_demod_freq = self._zurich_sample_read(
            self.sideband_sample_path
        )

        carrier_x, carrier_y = self._subtract_background_and_rotate(
            carrier_x_raw,
            carrier_y_raw,
            self.carrier_xbkg,
            self.carrier_ybkg,
            self.carrier_phase_rotation,
        )
        sideband_x, sideband_y = self._subtract_background_and_rotate(
            sideband_x_raw,
            sideband_y_raw,
            self.sideband_xbkg,
            self.sideband_ybkg,
            self.sideband_phase_rotation,
        )

        drive_readback = self._zurich_get_carrier_amp()
        sideband_drive_readback = self._zurich_get_sideband_amp()
        carrier_freq_readback = self._zurich_get_freq(self.carrier_osc_index)
        sideband_freq_readback = self._zurich_get_freq(self.sideband_osc_index)

        q_infer = np.nan
        f0_infer = np.nan
        tau_infer = np.nan
        sideband_q_infer = np.nan
        sideband_f0_infer = np.nan
        sideband_tau_infer = np.nan

        if drive_readback > 0 and carrier_x != 0:
            k_effective = self.carrier_k * self.carrier_V0 / drive_readback
            q_infer = calculate_Q_infer(carrier_x, carrier_y, k_effective)
            if np.isfinite(q_infer) and q_infer != 0:
                f0_infer = calculate_f0_infer(
                    carrier_x, carrier_y, carrier_demod_freq, k_effective
                )
                if np.isfinite(f0_infer) and f0_infer != 0:
                    tau_infer = q_infer / (np.pi * f0_infer)

        if sideband_drive_readback > 0 and sideband_x != 0:
            sideband_k_effective = (
                self.sideband_k * self.sideband_V0 / sideband_drive_readback
            )
            sideband_q_infer = calculate_Q_infer(
                sideband_x, sideband_y, sideband_k_effective
            )
            if np.isfinite(sideband_q_infer) and sideband_q_infer != 0:
                sideband_f0_infer = calculate_f0_infer(
                    sideband_x,
                    sideband_y,
                    sideband_demod_freq,
                    sideband_k_effective,
                )
                if np.isfinite(sideband_f0_infer) and sideband_f0_infer != 0:
                    sideband_tau_infer = sideband_q_infer / (np.pi * sideband_f0_infer)

        carrier_r = np.hypot(carrier_x, carrier_y)
        carrier_phase = np.degrees(np.arctan2(carrier_y, carrier_x))
        sideband_r = np.hypot(sideband_x, sideband_y)
        sideband_phase = np.degrees(np.arctan2(sideband_y, sideband_x))

        return {
            "utc": time.time(),
            "carrier_drive_readback": float(drive_readback),
            "sideband_drive_readback": float(sideband_drive_readback),
            "f_carrier_readback": float(carrier_freq_readback),
            "f_sideband_osc_set": float(
                self._sideband_freq_for_carrier(carrier_freq_readback)
            ),
            "f_sideband_osc_readback": float(sideband_freq_readback),
            "f_sideband_target": float(self.fixed_sideband_demod_freq),
            "f_sideband_sum_readback": float(carrier_freq_readback + sideband_freq_readback),
            "carrier_Q_infer": float(q_infer),
            "carrier_f0_infer": float(f0_infer),
            "carrier_tau_infer": float(tau_infer),
            "sideband_Q_infer": float(sideband_q_infer),
            "sideband_f0_infer": float(sideband_f0_infer),
            "sideband_tau_infer": float(sideband_tau_infer),
            "carrier_X": float(carrier_x),
            "carrier_Y": float(carrier_y),
            "carrier_R": float(carrier_r),
            "carrier_phase": float(carrier_phase),
            "carrier_demod_freq": float(carrier_demod_freq),
            "sideband_demod_freq": float(sideband_demod_freq),
            "sideband_X": float(sideband_x),
            "sideband_Y": float(sideband_y),
            "sideband_R": float(sideband_r),
            "sideband_phase": float(sideband_phase),
        }

    def _update_last_tau(self, measurement: dict) -> None:
        tau = measurement.get("carrier_tau_infer")
        if np.isfinite(tau) and tau > 0:
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
            try:
                time.sleep(min(0.1, remaining))
            except KeyboardInterrupt:
                return False

    def _build_drive_points(self) -> np.ndarray:
        if self.logspace:
            return np.logspace(
                np.log10(self.start_drive),
                np.log10(self.end_drive),
                int(self.num_points),
            )
        return np.linspace(self.start_drive, self.end_drive, int(self.num_points))

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
        if not self.use_drive_freq_csv:
            if self.num_points <= 0:
                raise ValueError("Number of points must be >= 1.")
            if self.start_drive < 0 or self.end_drive < 0:
                raise ValueError("Drive voltages must be >= 0.")
            if self.logspace and (self.start_drive <= 0 or self.end_drive <= 0):
                raise ValueError("Logspace sweep requires positive start/end drive.")
        if self.phase_band < 0:
            raise ValueError("Phase band must be >= 0.")
        if self.max_iterations <= 0:
            raise ValueError("Max retune iterations must be >= 1.")
        if self.fixed_delay_time < 0:
            raise ValueError("Fixed delay time must be >= 0.")
        if not self.use_drive_freq_csv:
            if not self.use_current_frequency and self.initial_frequency <= 0:
                raise ValueError(
                    "Initial frequency must be > 0 if not using current frequency."
                )
        if self.fixed_sideband_demod_freq <= 0:
            raise ValueError("Fixed sideband demod frequency must be > 0.")
        if min(
            self.carrier_osc_num,
            self.carrier_demod_num,
            self.sideband_osc_num,
            self.sideband_demod_num,
        ) <= 0:
            raise ValueError("Oscillator and demodulator numbers are 1-based and must be > 0.")
        mode = self._normalize_delay_mode(self.delay_mode)
        if mode not in ("fixed", "max", "tau"):
            raise ValueError("Delay mode must be one of: fixed, max, tau.")
        if self.use_drive_freq_csv and not str(self.drive_freq_csv_path).strip():
            raise ValueError("CSV path must be set when drive settings from csv is enabled.")

    def _load_drive_freq_csv(self) -> Tuple[List[float], List[float]]:
        path = Path(str(self.drive_freq_csv_path)).expanduser()
        if not path.exists():
            raise ValueError(f"CSV file not found: {path}")
        drives: List[float] = []
        freqs: List[float] = []
        with path.open("r", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=",")
            if reader.fieldnames is None:
                raise ValueError("CSV must include headers: drive_voltage, drive_frequency.")
            required = {"drive_voltage", "drive_frequency"}
            if not required.issubset(set(reader.fieldnames)):
                raise ValueError(
                    "CSV headers must include drive_voltage and drive_frequency."
                )
            for row in reader:
                drive_text = row.get("drive_voltage", "")
                freq_text = row.get("drive_frequency", "")
                if drive_text is None or freq_text is None:
                    raise ValueError("CSV rows must include drive_voltage and drive_frequency.")
                drive = float(str(drive_text).strip())
                freq = float(str(freq_text).strip())
                if drive < 0:
                    raise ValueError("CSV drive_voltage entries must be >= 0.")
                if freq <= 0:
                    raise ValueError("CSV drive_frequency entries must be > 0.")
                self._sideband_freq_for_carrier(freq)
                drives.append(drive)
                freqs.append(freq)
        if not drives:
            raise ValueError("CSV contains no drive/frequency rows.")
        return drives, freqs

    def _zurich_sample_read(self, sample_path: str):
        resp = self.daq.getSample(sample_path)
        return resp["x"][0], resp["y"][0], resp["frequency"][0]

    def _zurich_get_carrier_amp(self) -> float:
        amp_path = f"/{self.zur_id}/mods/0/carrier/amplitude"
        return self.daq.getDouble(amp_path)

    def _zurich_get_sideband_amp(self) -> float:
        amp_path = f"/{self.zur_id}/mods/0/sidebands/1/amplitude"
        return self.daq.getDouble(amp_path)

    def _zurich_get_freq(self, osc_num: int) -> float:
        osc_path = f"/{self.zur_id}/oscs/{osc_num}/freq"
        return self.daq.getDouble(osc_path)

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

    def _sideband_freq_for_carrier(self, carrier_freq: float) -> float:
        sideband_freq = float(self.fixed_sideband_demod_freq) - float(carrier_freq)
        if sideband_freq < 0:
            raise ValueError(
                "Carrier frequency %.9g Hz makes the sideband oscillator frequency "
                "negative for fixed sideband target %.9g Hz."
                % (carrier_freq, self.fixed_sideband_demod_freq)
            )
        return sideband_freq

    def _set_drive_and_sideband(self, amplitude: float, carrier_freq: float):
        sideband_freq = self._sideband_freq_for_carrier(carrier_freq)
        amp_path = f"/{self.zur_id}/mods/0/carrier/amplitude"
        carrier_freq_path = f"/{self.zur_id}/oscs/{self.carrier_osc_index}/freq"
        sideband_freq_path = f"/{self.zur_id}/oscs/{self.sideband_osc_index}/freq"
        self.daq.setDouble(amp_path, float(amplitude))
        self.daq.setDouble(sideband_freq_path, float(sideband_freq))
        self.daq.setDouble(carrier_freq_path, float(carrier_freq))
        self.daq.sync()

    def _restore_drive_state(self):
        amp_path = f"/{self.zur_id}/mods/0/carrier/amplitude"
        carrier_freq_path = f"/{self.zur_id}/oscs/{self.carrier_osc_index}/freq"
        sideband_freq_path = f"/{self.zur_id}/oscs/{self.sideband_osc_index}/freq"
        self.daq.setDouble(amp_path, float(self.restore_amp))
        self.daq.setDouble(carrier_freq_path, float(self.restore_carrier_freq))
        self.daq.setDouble(sideband_freq_path, float(self.restore_sideband_freq))
        self.daq.sync()


class ResonantDriveSweepSidebandWindow(ManagedDockWindow):
    def __init__(self):
        drive_plot = PlotWidget(
            name="Drive Sweep",
            columns=ResonantDriveSweepSidebandProcedure.DATA_COLUMNS,
            x_axis="carrier_drive_set",
            y_axis="carrier_f0_infer",
        )
        phase_plot = PlotWidget(
            name="Carrier Phase",
            columns=ResonantDriveSweepSidebandProcedure.DATA_COLUMNS,
            x_axis="carrier_drive_set",
            y_axis="carrier_phase",
        )
        q_plot = PlotWidget(
            name="Q",
            columns=ResonantDriveSweepSidebandProcedure.DATA_COLUMNS,
            x_axis="carrier_drive_set",
            y_axis="carrier_Q_infer",
        )
        carrier_nyquist_plot = PlotWidget(
            name="Carrier Nyquist",
            columns=ResonantDriveSweepSidebandProcedure.DATA_COLUMNS,
            x_axis="carrier_X",
            y_axis="carrier_Y",
        )
        carrier_nyquist_plot.plot.getViewBox().setAspectLocked(True, ratio=1.0)
        sideband_plot = PlotWidget(
            name="Sideband Response",
            columns=ResonantDriveSweepSidebandProcedure.DATA_COLUMNS,
            x_axis="carrier_drive_set",
            y_axis="sideband_R",
        )
        sideband_nyquist_plot = PlotWidget(
            name="Sideband Nyquist",
            columns=ResonantDriveSweepSidebandProcedure.DATA_COLUMNS,
            x_axis="sideband_X",
            y_axis="sideband_Y",
        )
        sideband_nyquist_plot.plot.getViewBox().setAspectLocked(True, ratio=1.0)

        super().__init__(
            procedure_class=ResonantDriveSweepSidebandProcedure,
            inputs=ResonantDriveSweepSidebandProcedure.PARAMETERS,
            displays=ResonantDriveSweepSidebandProcedure.PARAMETERS,
            x_axis=["carrier_drive_readback"],
            y_axis=["carrier_Q_infer", "carrier_f0_infer", "carrier_phase", "sideband_R"],
            widget_list=(
                drive_plot,
                phase_plot,
                q_plot,
                carrier_nyquist_plot,
                sideband_plot,
                sideband_nyquist_plot,
            ),
            inputs_in_scrollarea=True,
        )
        self.setWindowTitle("Zurich Resonant Drive Sweep Sideband")

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
    window = ResonantDriveSweepSidebandWindow()
    window.show()
    sys.exit(app.exec_())
