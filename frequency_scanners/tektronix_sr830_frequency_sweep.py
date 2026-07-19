import logging
import re
import sys
import time

import numpy as np
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
from pymeasure.instruments.srs import SR830
from pymeasure.instruments.tektronix import AFG3152C


log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

DEFAULT_DATA_DIRECTORY = r"D:/Data/Fall25-Summer26/SQUID bode"

_SHORT_GPIB_ADDRESS = re.compile(r"^(\d+)::(\d+)$")
_FULL_GPIB_ADDRESS = re.compile(r"^GPIB(\d+)::(\d+)::INSTR$", re.IGNORECASE)


def gpib_resource(address: str) -> str:
    """Convert a ``bus::address`` entry to a complete VISA resource name."""
    address = str(address).strip()
    match = _SHORT_GPIB_ADDRESS.fullmatch(address)
    if match:
        bus, instrument = match.groups()
        return f"GPIB{bus}::{instrument}::INSTR"

    match = _FULL_GPIB_ADDRESS.fullmatch(address)
    if match:
        bus, instrument = match.groups()
        return f"GPIB{bus}::{instrument}::INSTR"

    raise ValueError(
        f"Invalid GPIB address {address!r}. Use 'bus::address', for example '2::9'."
    )


def frequency_points(
    start_frequency: float,
    stop_frequency: float,
    number_of_points: int,
    log_sweep: bool,
) -> np.ndarray:
    """Return endpoint-inclusive logarithmic or linear frequency points."""
    start_frequency = float(start_frequency)
    stop_frequency = float(stop_frequency)
    number_of_points = int(number_of_points)

    if start_frequency <= 0 or stop_frequency <= 0:
        raise ValueError("Start and stop frequencies must both be > 0 Hz.")
    if start_frequency == stop_frequency:
        raise ValueError("Start and stop frequencies must be different.")
    if number_of_points < 2:
        raise ValueError("Number of points must be at least 2.")

    if log_sweep:
        return np.geomspace(start_frequency, stop_frequency, number_of_points)
    return np.linspace(start_frequency, stop_frequency, number_of_points)


class TektronixSR830FrequencySweep(Procedure):
    start_frequency = FloatParameter(
        "Start frequency", units="Hz", default=10.0, minimum=1e-6
    )
    stop_frequency = FloatParameter(
        "Stop frequency", units="Hz", default=30e3, minimum=1e-6
    )
    number_of_points = IntegerParameter("Number of points", default=10, minimum=2)
    log_sweep = BooleanParameter("Logarithmic sweep", default=True)

    drive_amplitude = FloatParameter(
        "Tektronix channel 1 drive amplitude",
        units="Vrms",
        default=1.0,
        minimum=7e-3,
        maximum=3.536,
    )
    sr830_time_constant = FloatParameter(
        "SR830 time constant", units="s", default=1.0, minimum=10e-6
    )
    delay = FloatParameter(
        "Delay after setting frequency", units="s", default=10.0, minimum=0.0
    )

    tektronix_address = Parameter(
        "Tektronix GPIB address (bus::address)", default="1::11"
    )
    sr830_address = Parameter("SR830 GPIB address (bus::address)", default="2::9")
    file_prefix = Parameter("File prefix", default="tektronix_sr830_frequency_sweep")
    comments = Parameter("Comments/Notes", default="")

    PARAMETERS = [
        "start_frequency",
        "stop_frequency",
        "number_of_points",
        "log_sweep",
        "drive_amplitude",
        "sr830_time_constant",
        "delay",
        "tektronix_address",
        "sr830_address",
        "file_prefix",
        "comments",
    ]

    DATA_COLUMNS = [
        "UTC",
        "timestamp",
        "frequency_set",
        "frequency",
        "X",
        "Y",
        "R",
        "theta",
        "drive_amplitude_vrms",
        "sr830_time_constant",
    ]

    def startup(self):
        self._validate_parameters()
        self.frequencies = frequency_points(
            self.start_frequency,
            self.stop_frequency,
            self.number_of_points,
            self.log_sweep,
        )

        tektronix_resource = gpib_resource(self.tektronix_address)
        sr830_resource = gpib_resource(self.sr830_address)
        log.info("Tektronix address: %s", tektronix_resource)
        log.info("SR830 address: %s", sr830_resource)

        self.afg = AFG3152C(tektronix_resource)
        self.lockin = SR830(sr830_resource)

        self.afg.ch1.unit = "VRMS"
        self.afg.ch1.amp_vrms = float(self.drive_amplitude)
        self.actual_drive_amplitude = float(self.afg.ch1.amp_vrms)
        log.info(
            "Tektronix channel 1 drive amplitude: %.9g Vrms",
            self.actual_drive_amplitude,
        )

        self.lockin.time_constant = float(self.sr830_time_constant)
        self.actual_time_constant = float(self.lockin.time_constant)
        if not np.isclose(self.actual_time_constant, self.sr830_time_constant):
            log.warning(
                "The SR830 changed the requested %.6g s time constant to "
                "its supported %.6g s setting.",
                self.sr830_time_constant,
                self.actual_time_constant,
            )

        if self.delay < 10 * self.actual_time_constant:
            log.warning(
                "The %.6g s delay is less than 10 times the SR830 time "
                "constant (%.6g s).",
                self.delay,
                self.actual_time_constant,
            )

        sweep_type = "logarithmic" if self.log_sweep else "linear"
        log.info(
            "Starting %s sweep with %d points from %.9g Hz to %.9g Hz",
            sweep_type,
            len(self.frequencies),
            self.frequencies[0],
            self.frequencies[-1],
        )
        log.info(
            "Estimated settling time: %.3f minutes",
            len(self.frequencies) * self.delay / 60,
        )
        self.t_start = time.time()

    def execute(self):
        for index, frequency_set in enumerate(self.frequencies):
            if self.should_stop():
                log.warning("Stop requested before frequency point %d", index)
                break

            self.afg.ch1.frequency = float(frequency_set)
            log.info("Set Tektronix frequency to %.9g Hz", frequency_set)

            if not self._sleep_with_abort(float(self.delay)):
                log.warning("Stop requested during the settling delay")
                break

            frequency_readback = self._read_afg_frequency()
            x, y = (float(value) for value in self.lockin.xy)
            utc_time = time.time()

            data = {
                "UTC": utc_time,
                "timestamp": utc_time - self.t_start,
                "frequency_set": float(frequency_set),
                "frequency": frequency_readback,
                "X": x,
                "Y": y,
                "R": float(np.hypot(x, y)),
                "theta": float(np.degrees(np.arctan2(y, x))),
                "drive_amplitude_vrms": self.actual_drive_amplitude,
                "sr830_time_constant": self.actual_time_constant,
            }
            self.emit("results", data)
            self.emit("progress", 100 * (index + 1) / len(self.frequencies))

    def shutdown(self):
        for instrument_name in ("lockin", "afg"):
            instrument = getattr(self, instrument_name, None)
            if instrument is None:
                continue
            try:
                instrument.adapter.close()
            except Exception:
                log.exception("Unable to close the %s connection", instrument_name)

    def _read_afg_frequency(self, attempts: int = 5) -> float:
        for attempt in range(1, attempts + 1):
            try:
                return float(self.afg.ch1.frequency)
            except Exception:
                if attempt == attempts:
                    raise
                log.warning(
                    "Tektronix frequency query failed (attempt %d/%d); retrying",
                    attempt,
                    attempts,
                )
                if not self._sleep_with_abort(0.1):
                    raise RuntimeError(
                        "Stopped while retrying the Tektronix frequency query"
                    )
        raise RuntimeError("Tektronix frequency query failed")

    def _sleep_with_abort(self, duration: float) -> bool:
        end_time = time.time() + max(0.0, duration)
        while True:
            if self.should_stop():
                return False
            remaining = end_time - time.time()
            if remaining <= 0:
                return True
            time.sleep(min(0.1, remaining))

    def _validate_parameters(self):
        frequency_points(
            self.start_frequency,
            self.stop_frequency,
            self.number_of_points,
            self.log_sweep,
        )
        if self.delay < 0:
            raise ValueError("Delay must be >= 0 s.")
        if not 7e-3 <= self.drive_amplitude <= 3.536:
            raise ValueError(
                "Tektronix drive amplitude must be between 0.007 and 3.536 Vrms."
            )
        if not 10e-6 <= self.sr830_time_constant <= 30e3:
            raise ValueError("SR830 time constant must be between 10 us and 30,000 s.")
        gpib_resource(self.tektronix_address)
        gpib_resource(self.sr830_address)


class TektronixSR830FrequencySweepWindow(ManagedDockWindow):
    def __init__(self):
        magnitude_plot = PlotWidget(
            name="R vs Frequency",
            columns=TektronixSR830FrequencySweep.DATA_COLUMNS,
            x_axis="frequency",
            y_axis="R",
        )
        phase_plot = PlotWidget(
            name="Theta vs Frequency",
            columns=TektronixSR830FrequencySweep.DATA_COLUMNS,
            x_axis="frequency",
            y_axis="theta",
        )

        super().__init__(
            procedure_class=TektronixSR830FrequencySweep,
            inputs=TektronixSR830FrequencySweep.PARAMETERS,
            displays=TektronixSR830FrequencySweep.PARAMETERS,
            x_axis=["frequency"],
            y_axis=["X", "Y", "R", "theta"],
            widget_list=(magnitude_plot, phase_plot),
            # directory_input=True,
            inputs_in_scrollarea=True,
        )
        self.setWindowTitle("Tektronix AFG and SR830 Frequency Sweep")
        self.directory = DEFAULT_DATA_DIRECTORY

    def queue(self):
        procedure = self.make_procedure()
        prefix = f"{procedure.file_prefix}_" if procedure.file_prefix else ""
        filename = unique_filename(self.directory, prefix=prefix)
        results = Results(procedure, filename)
        experiment = self.new_experiment(results)
        self.manager.queue(experiment)


def main():
    app = QtWidgets.QApplication(sys.argv)
    window = TektronixSR830FrequencySweepWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
