import csv
import logging
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import zhinst.core
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


def calculate_Q_infer(x: float, y: float, k: float) -> float:
    return k * (x**2 + y**2) / x


def calculate_f0_infer(x: float, y: float, f_drive: float, k: float) -> float:
    q = calculate_Q_infer(x, y, k)
    return f_drive * (1 + y / (x * 2 * q))


class ResonantDriveSweepProcedure(Procedure):
    """Sweep drive amplitude while retuning to resonance at each point."""

    k = FloatParameter("k constant", units="1/V", default=93260373.7208108)
    V0 = FloatParameter("Drive that k was obtained at", units="V", default=267.6e-6)
    xbkg = FloatParameter("X background", units="V", default=-7.897e-05)
    ybkg = FloatParameter("Y background", units="V", default=-5.93456e-05)

    start_drive = FloatParameter("Start drive", units="V", default=0.1e-3)
    end_drive = FloatParameter("End drive", units="V", default=10e-3)
    num_points = IntegerParameter("Number of points", default=10)
    logspace = BooleanParameter("Log10 grid", default=False)
    use_drive_freq_csv = BooleanParameter("Drive settings from csv", default=False)
    drive_freq_csv_path = Parameter("Drive/freq csv path", default="")

    reverse_sweep = BooleanParameter("Reverse sweep", default=True)
    retune_down_sweep = BooleanParameter("Retune to resonance on down sweep", default=False)

    phase_band = FloatParameter("Phase band", units="deg", default=5.0)
    max_iterations = IntegerParameter("Max retune iterations", default=5)

    use_current_frequency = BooleanParameter("Use current frequency", default=True)
    initial_frequency = FloatParameter("Initial frequency", units="Hz", default=0.0)

    fixed_delay_time = FloatParameter("Fixed delay time", units="s", default=500)
    delay_mode = Parameter("Delay mode (fixed|max|tau)", default="fixed")

    file_prefix = Parameter("File prefix", default="resonant_drive_sweep")

    zur_id = Parameter("Zurich addr.", default="dev4934")
    osc_num = IntegerParameter("Oscillator number", default=2)
    demod_num = IntegerParameter("Demodulator number", default=1)
    server_host = Parameter("Server host", default="192.168.77.26")
    server_port = IntegerParameter("Server port", default=8004)
    interface = Parameter("Interface", default="PCIe")
    comments = Parameter("Comments/Notes", default="")

    PARAMETERS = [
        "k",
        "V0",
        "xbkg",
        "ybkg",
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
        "osc_num",
        "demod_num",
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
        "drive_set",
        "drive_readback",
        "f_drive_set",
        "f_drive_readback",
        "Q_infer",
        "f0_infer",
        "tau_infer",
        "X",
        "Y",
        "R",
        "phase",
        "in_band",
        "iterations",
        "retuned",
        "delay_used",
    ]

    def startup(self):
        log.info("Starting resonant drive sweep")
        self._validate_parameters()

        self.osc_index = self.osc_num - 1
        self.demod_index = self.demod_num - 1

        self.daq = zhinst.core.ziDAQServer(
            self.server_host, int(self.server_port), api_level=6
        )
        self.daq.connectDevice(self.zur_id, interface=self.interface)
        self.sample_path = f"/{self.zur_id}/demods/{self.demod_index}/sample"
        self.daq.set(f"/{self.zur_id}/demods/{self.demod_index}/enable", 1)

        self.restore_amp = self._zurich_get_amp(self.osc_index)
        self.restore_freq = self._zurich_get_freq(self.osc_index)

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
            current_freq = self._zurich_get_freq(self.osc_index)
        elif self.use_current_frequency:
            current_freq = self._zurich_get_freq(self.osc_index)
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
            self._set_drive(self.restore_amp, self.restore_freq)
            log.info("Restored original drive settings")
        except Exception:
            log.exception("Unable to restore original drive settings")

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

            self._set_drive(drive, current_freq)
            last_delay = self._delay_after_set(self.last_tau)
            if not self._sleep_with_abort(last_delay):
                return current_freq, False

            measurement = self._measure_once()
            iterations += 1

            in_band = abs(measurement["phase"]) <= self.phase_band
            self._update_last_tau(measurement)

            measurement.update(
                {
                    "step_index": int(self.step_index),
                    "drive_index": int(drive_index),
                    "sweep_direction": int(sweep_direction),
                    "drive_set": float(drive),
                    "f_drive_set": float(current_freq),
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

            f0_infer = measurement["f0_infer"]
            if not np.isfinite(f0_infer):
                return current_freq, True

            current_freq = float(f0_infer)
            retuned = True

    def _measure_once(self) -> dict:
        x_raw, y_raw, f_meas = self._zurich_sample_read()
        x = x_raw - self.xbkg
        y = y_raw - self.ybkg
        drive_readback = self._zurich_get_amp(self.osc_index)
        f_readback = self._zurich_get_freq(self.osc_index)

        k_effective = np.nan
        q_infer = np.nan
        f0_infer = np.nan
        tau_infer = np.nan

        if drive_readback > 0 and x != 0:
            k_effective = self.k * self.V0 / drive_readback
            q_infer = calculate_Q_infer(x, y, k_effective)
            if np.isfinite(q_infer) and q_infer != 0:
                f0_infer = calculate_f0_infer(x, y, f_meas, k_effective)
                if np.isfinite(f0_infer) and f0_infer != 0:
                    tau_infer = q_infer / (np.pi * f0_infer)

        data = {
            "utc": time.time(),
            "drive_readback": float(drive_readback),
            "f_drive_readback": float(f_readback),
            "Q_infer": float(q_infer),
            "f0_infer": float(f0_infer),
            "tau_infer": float(tau_infer),
            "X": float(x),
            "Y": float(y),
            "R": float(np.sqrt(x**2 + y**2)),
            "phase": float(np.degrees(np.arctan2(y, x))),
        }
        return data

    def _update_last_tau(self, measurement: dict) -> None:
        tau = measurement.get("tau_infer")
        if tau is None:
            return
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
        if self.osc_num <= 0 or self.demod_num <= 0:
            raise ValueError("osc_num and demod_num are 1-based and must be > 0.")
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
                drives.append(drive)
                freqs.append(freq)
        if not drives:
            raise ValueError("CSV contains no drive/frequency rows.")
        return drives, freqs

    def _zurich_sample_read(self):
        resp = self.daq.getSample(self.sample_path)
        x = resp["x"][0]
        y = resp["y"][0]
        f = resp["frequency"][0]
        return x, y, f

    def _zurich_get_amp(self, osc_num: int) -> float:
        osc_path = f"/{self.zur_id}/sigouts/0/amplitudes/{osc_num}"
        return self.daq.getDouble(osc_path)

    def _zurich_get_freq(self, osc_num: int) -> float:
        osc_path = f"/{self.zur_id}/oscs/{osc_num}/freq"
        return self.daq.getDouble(osc_path)

    def _set_drive(self, amplitude: float, frequency: float):
        amp_path = f"/{self.zur_id}/sigouts/0/amplitudes/{self.osc_index}"
        freq_path = f"/{self.zur_id}/oscs/{self.osc_index}/freq"
        self.daq.setDouble(amp_path, float(amplitude))
        self.daq.setDouble(freq_path, float(frequency))
        self.daq.sync()


class ResonantDriveSweepWindow(ManagedDockWindow):
    def __init__(self):
        drive_plot = PlotWidget(
            name="Drive Sweep",
            columns=ResonantDriveSweepProcedure.DATA_COLUMNS,
            x_axis="drive_set",
            y_axis="f0_infer",
        )
        phase_plot = PlotWidget(
            name="Phase",
            columns=ResonantDriveSweepProcedure.DATA_COLUMNS,
            x_axis="drive_set",
            y_axis="phase",
        )
        q_plot = PlotWidget(
            name="Q",
            columns=ResonantDriveSweepProcedure.DATA_COLUMNS,
            x_axis="drive_set",
            y_axis="Q_infer",
        )
        nyquist_plot = PlotWidget(
            name="Nyquist",
            columns=ResonantDriveSweepProcedure.DATA_COLUMNS,
            x_axis="X",
            y_axis="Y",
        )
        nyquist_plot.plot.getViewBox().setAspectLocked(True, ratio=1.0)

        super().__init__(
            procedure_class=ResonantDriveSweepProcedure,
            inputs=ResonantDriveSweepProcedure.PARAMETERS,
            displays=ResonantDriveSweepProcedure.PARAMETERS,
            x_axis=["drive_readback"],
            y_axis=["Q_infer", "f0_infer", "phase"],
            widget_list=(drive_plot, phase_plot, q_plot, nyquist_plot),
            inputs_in_scrollarea=True,
            # directory_input=True,
        )
        self.setWindowTitle("Zurich Resonant Drive Sweep")
        # self.directory = "data-files"

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
    window = ResonantDriveSweepWindow()
    window.show()
    sys.exit(app.exec_())
