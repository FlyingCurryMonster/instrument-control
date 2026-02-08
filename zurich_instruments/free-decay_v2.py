import logging
import sys
import time
from pathlib import Path
from typing import Optional

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


class FreeDecayProcedure(Procedure):
    """Drop the drive to zero and record the demodulated response."""

    # How many decay captures to perform
    iterations = IntegerParameter("Loop Iterations", default=1)

    # Wait before the first drop when using the current drive
    delay_before_drop = FloatParameter("Initial delay", units="s", default=0.2)

    # Time spent measuring a single decay
    measurement_time = FloatParameter("Measurement time", units="s", default=300)

    # Time to allow the resonator to ring up between drops
    ring_up_time = FloatParameter("Ring-up time", units="s", default=300)

    # Drive selection
    use_current_drive = BooleanParameter(
        "Use current drive amplitude/frequency", default=True
    )
    initial_voltage = FloatParameter("Initial Voltage", units="V")
    initial_frequency = FloatParameter("Initial Drive Frequency", units="Hz")
    settle_after_set = FloatParameter("Wait after setting drive", units="s")

    # Readout cadence for getSample loop: 1 Hz = one sample read per second
    sample_rate = FloatParameter("Sample rate", units="Hz", default=1.0)

    # Zurich connection
    zur_id = Parameter("Zurich addr.", default="dev4934")
    # Lab uses 1-based numbering; defaults mirror the PLL script (osc=2, demod=1)
    osc_num = IntegerParameter("Oscillator number", default=2)
    demod_num = IntegerParameter("Demodulator number", default=1)
    server_host = Parameter("Server host", default="192.168.77.26")
    server_port = IntegerParameter("Server port", default=8004)
    interface = Parameter("Interface", default="PCIe")
    comments = Parameter("Comments/Notes", default="")

    PARAMETERS = [
        "iterations",
        "delay_before_drop",
        "measurement_time",
        "ring_up_time",
        "use_current_drive",
        "initial_voltage",
        "initial_frequency",
        "settle_after_set",
        "sample_rate",
        "zur_id",
        "osc_num",
        "demod_num",
        "server_host",
        "server_port",
        "interface",
        "comments",
    ]

    DATA_COLUMNS = [
        "iteration",
        "t_rel",
        "utc",
        "x",
        "y",
        "phase_deg",
        "frequency",
        "drive_before_drop",
        "drive_freq_setpoint",
    ]

    def startup(self):
        log.info("Connecting to Zurich Instrument for free decay capture")

        # Initialize so shutdown can safely reference them even on startup failure
        self.restore_amp = None
        self.restore_freq = None

        self.osc_index = self.osc_num - 1
        self.demod_index = self.demod_num - 1

        self.daq = zhinst.core.ziDAQServer(
            self.server_host, int(self.server_port), api_level=6
        )
        self.daq.connectDevice(self.zur_id, interface=self.interface)
        self.sample_path = f"/{self.zur_id}/demods/{self.demod_index}/sample"

        # Ensure the demodulator is enabled
        self.daq.set(f"/{self.zur_id}/demods/{self.demod_index}/enable", 1)

        self.clockbase = self.daq.getInt(f"/{self.zur_id}/clockbase")

        # Remember current settings so we can restore them later
        self.restore_amp = self._zurich_get_amp(self.osc_index)
        self.restore_freq = self._zurich_get_freq(self.osc_index)

        if self.use_current_drive:
            self.target_amp = self.restore_amp
            self.target_freq = self.restore_freq
            self.pre_drop_wait = self.delay_before_drop
            log.info(
                "Using current drive settings (%.3g V, %.3f Hz)",
                self.target_amp,
                self.target_freq,
            )
        else:
            if self.initial_voltage <= 0:
                raise ValueError("Initial voltage must be > 0 when not using current drive.")
            if self.initial_frequency <= 0:
                raise ValueError("Initial frequency must be > 0 when not using current drive.")
            self.target_amp = self.initial_voltage
            self.target_freq = self.initial_frequency
            self.pre_drop_wait = self.settle_after_set

            self._set_drive(self.target_amp, self.target_freq)
            log.info(
                "Set drive to %.3g V @ %.3f Hz; will wait %.2f s before first drop",
                self.target_amp,
                self.target_freq,
                self.pre_drop_wait,
            )

    def execute(self):
        for iteration in range(self.iterations):
            if self.should_stop():
                log.warning("Stop requested before iteration %d", iteration)
                break

            # Wait appropriate time before the drop
            if iteration == 0:
                wait_time = self.pre_drop_wait
            else:
                wait_time = self.ring_up_time

            if wait_time > 0:
                self._sleep_with_abort(wait_time)
                if self.should_stop():
                    log.warning("Stop requested while waiting before iteration %d", iteration)
                    break

            # Capture the drive settings right before we cut the drive
            pre_drop_amp = self._zurich_get_amp(self.osc_index)
            pre_drop_freq = self._zurich_get_freq(self.osc_index)

            # Set drive to zero and start capturing immediately
            self._set_drive(0.0, pre_drop_freq, amplitude_only=True)
            drop_time_utc = time.time()

            log.info("Starting decay %d/%d", iteration + 1, self.iterations)
            self._record_decay(
                iteration,
                drop_time_utc,
                pre_drop_amp,
                pre_drop_freq,
            )

            progress = 100 * (iteration + 1) / self.iterations
            self.emit("progress", progress)

            if self.should_stop():
                log.warning("Stop requested after iteration %d", iteration)
                break

            # Restore drive so the resonator can ring up for the next decay
            if iteration < self.iterations - 1:
                self._set_drive(self.target_amp, self.target_freq)

        self.emit("progress", 100)

    def shutdown(self):
        log.info("Restoring original drive settings")
        try:
            if self.restore_amp is not None and self.restore_freq is not None:
                self._set_drive(self.restore_amp, self.restore_freq)
        except Exception:
            log.exception("Unable to restore the original drive settings.")
        log.info("Finished")

    def check_parameters(self):
        """Allow optional drive params when using current drive; validate otherwise."""
        params = self.parameter_objects()

        def ensure_set(name):
            if params[name].value is None:
                raise NameError(f"Missing value for '{name}'")

        # Required in all modes
        for required in [
            "iterations",
            "delay_before_drop",
            "measurement_time",
            "ring_up_time",
            "use_current_drive",
            "sample_rate",
            "zur_id",
            "osc_num",
            "demod_num",
            "server_host",
            "server_port",
            "interface",
        ]:
            ensure_set(required)

        if not self.use_current_drive:
            for opt in ["initial_voltage", "initial_frequency", "settle_after_set"]:
                ensure_set(opt)

        # Basic sanity checks
        if self.iterations <= 0:
            raise ValueError("iterations must be > 0")
        if self.delay_before_drop <= 0:
            raise ValueError("delay_before_drop must be > 0")
        if self.measurement_time <= 0:
            raise ValueError("measurement_time must be > 0")
        if self.ring_up_time <= 0:
            raise ValueError("ring_up_time must be > 0")
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be > 0")
        if self.osc_num <= 0 or self.demod_num <= 0:
            raise ValueError("osc_num and demod_num are 1-based and must be > 0")
        if not self.use_current_drive:
            if self.initial_voltage is None or self.initial_voltage <= 0:
                raise ValueError("initial_voltage must be > 0 when not using current drive.")
            if self.initial_frequency is None or self.initial_frequency <= 0:
                raise ValueError("initial_frequency must be > 0 when not using current drive.")
            if self.settle_after_set is None or self.settle_after_set <= 0:
                raise ValueError("settle_after_set must be > 0 when not using current drive.")

    # --- Zurich helpers -------------------------------------------------
    def _zurich_get_amp(self, osc_num: int) -> float:
        osc_path = f"/{self.zur_id}/sigouts/0/amplitudes/{osc_num}"
        return self.daq.getDouble(osc_path)

    def _zurich_get_freq(self, osc_num: int) -> float:
        osc_path = f"/{self.zur_id}/oscs/{osc_num}/freq"
        return self.daq.getDouble(osc_path)

    def _set_drive(self, amplitude: float, frequency: float, amplitude_only: bool = False):
        amp_path = f"/{self.zur_id}/sigouts/0/amplitudes/{self.osc_index}"
        self.daq.setDouble(amp_path, amplitude)
        if not amplitude_only:
            freq_path = f"/{self.zur_id}/oscs/{self.osc_index}/freq"
            self.daq.setDouble(freq_path, frequency)
        self.daq.sync()

    # --- Data handling --------------------------------------------------
    def _record_decay(
        self,
        iteration: int,
        drop_time_utc: float,
        pre_drop_amp: float,
        pre_drop_freq: float,
    ):
        """Run point-by-point getSample reads until measurement window is finished."""
        t_start = time.time()
        t_end = t_start + self.measurement_time
        sample_period = 1.0 / self.sample_rate

        while time.time() < t_end and not self.should_stop():
            loop_start = time.time()
            try:
                sample = self.daq.getSample(self.sample_path)
            except Exception:
                log.exception("Unable to read demod sample in iteration %d", iteration)
                sample = None

            self._emit_from_sample(
                sample,
                iteration=iteration,
                drop_time_utc=drop_time_utc,
                drive_before_drop=pre_drop_amp,
                drive_freq_setpoint=pre_drop_freq,
            )

            # Update progress within the iteration so the UI stays responsive.
            iter_frac = min(1.0, (time.time() - t_start) / self.measurement_time)
            overall = 100.0 * (iteration + iter_frac) / max(1, self.iterations)
            self.emit("progress", overall)

            elapsed = time.time() - loop_start
            remaining_sleep = sample_period - elapsed
            if remaining_sleep > 0:
                self._sleep_with_abort(remaining_sleep)

    def _emit_from_sample(
        self,
        sample: Optional[dict],
        iteration: int,
        drop_time_utc: float,
        drive_before_drop: float,
        drive_freq_setpoint: float,
    ):
        """Transform getSample output into pymeasure result rows."""
        if not sample:
            log.warning("No demod sample data returned for iteration %d", iteration)
            return

        xs = np.asarray(sample.get("x", []), dtype=float)
        ys = np.asarray(sample.get("y", []), dtype=float)
        freqs = np.asarray(sample.get("frequency", []), dtype=float)

        n = min(len(xs), len(ys))
        if n == 0:
            log.warning("Empty demod vectors returned for iteration %d", iteration)
            return

        for idx in range(n):
            utc = time.time()
            t_rel = utc - drop_time_utc
            freq = freqs[idx] if idx < len(freqs) else np.nan
            phase_deg = np.degrees(np.arctan2(ys[idx], xs[idx]))
            data = {
                "iteration": iteration,
                "t_rel": float(t_rel),
                "utc": float(utc),
                "x": float(xs[idx]),
                "y": float(ys[idx]),
                "phase_deg": float(phase_deg),
                "frequency": float(freq),
                "drive_before_drop": float(drive_before_drop),
                "drive_freq_setpoint": float(drive_freq_setpoint),
            }
            self.emit("results", data)

    # --- Utility --------------------------------------------------------
    def _sleep_with_abort(self, duration: float):
        """Sleep in small chunks so stop requests are honored."""
        end_time = time.time() + duration
        while time.time() < end_time:
            if self.should_stop():
                break
            time.sleep(min(0.1, end_time - time.time()))


class FreeDecayWindow(ManagedDockWindow):
    def __init__(self):
        decay_plot = PlotWidget(
            name="Demod X vs time",
            columns=FreeDecayProcedure.DATA_COLUMNS,
            x_axis="t_rel",
            y_axis="x",
        )
        phase_plot = PlotWidget(
            name="Phase vs time",
            columns=FreeDecayProcedure.DATA_COLUMNS,
            x_axis="t_rel",
            y_axis="phase_deg",
        )

        super().__init__(
            procedure_class=FreeDecayProcedure,
            inputs=FreeDecayProcedure.PARAMETERS,
            displays=FreeDecayProcedure.PARAMETERS,
            x_axis=["t_rel"],
            y_axis=["x", "y", "phase_deg", "frequency"],
            widget_list=(decay_plot, phase_plot),
        )

        self.setWindowTitle("Zurich Free Decay Capture")
        self.filename_prefix = "free-decay"
        self.directory = self._default_data_dir()

    def queue(self):
        # directory.mkdir(parents=True, exist_ok=True)
        filename = unique_filename(str(self.directory), prefix=f"{self.filename_prefix}_")
        procedure = self.make_procedure()
        results = Results(procedure, filename)
        experiment = self.new_experiment(results)
        self.manager.queue(experiment)

    @staticmethod
    def _default_data_dir() -> Path:
        # Match PLL script convention: Fall25-Summer26/<experiment>
        preferred = Path(r"D:/Data/Fall25-Summer26/free-decay")
        if preferred.exists():
            return preferred
        fallback = Path(__file__).resolve().parent.parent / "data-files/free-decay"
        return fallback if fallback.exists() else Path.cwd()


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    window = FreeDecayWindow()
    window.show()
    sys.exit(app.exec_())
