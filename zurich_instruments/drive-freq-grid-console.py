"""Console sweep over drive voltages and frequencies for Zurich Instruments."""
import logging
import re
import sys
import time
from typing import List

import numpy as np
import zhinst.core
from pymeasure.display.console import ManagedConsole
from pymeasure.experiment import (
    FloatParameter,
    IntegerParameter,
    Parameter,
    Procedure,
)

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


class DriveFrequencyGridProcedure(Procedure):
    """Set drive amplitude/frequency pairs and log the demod X/Y response."""

    drive_voltages = Parameter("Drive voltages (comma/space)", default=None)
    drive_frequencies = Parameter("Drive frequencies (comma/space)", default=None)
    delay = FloatParameter("Delay after setting drive", units="s", default=600)

    zur_id = Parameter("Zurich addr.", default="dev4934")
    osc_num = IntegerParameter("Oscillator number", default=2)
    demod_num = IntegerParameter("Demodulator number", default=1)
    server_host = Parameter("Server host", default="192.168.77.26")
    server_port = IntegerParameter("Server port", default=8004)
    interface = Parameter("Interface", default="PCIe")
    comments = Parameter("Comments/Notes", default="")

    PARAMETERS = [
        "drive_voltages",
        "drive_frequencies",
        "delay",
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
        "voltage_index",
        "frequency_index",
        "utc",
        "voltage_set",
        "frequency_set",
        "voltage_readback",
        "frequency_readback",
        "frequency_measured",
        "x",
        "y",
        "r",
        "phase_deg",
    ]

    def startup(self):
        log.info("Connecting to Zurich Instrument")
        self.restore_amp = None
        self.restore_freq = None

        self.osc_index = self.osc_num - 1
        self.demod_index = self.demod_num - 1
        if self.osc_index < 0 or self.demod_index < 0:
            raise ValueError("osc_num and demod_num are 1-based and must be > 0")

        self.voltages = self._parse_list(self.drive_voltages, "drive_voltages")
        self.frequencies = self._parse_list(self.drive_frequencies, "drive_frequencies")

        self.total_steps = len(self.voltages) * len(self.frequencies)
        if self.total_steps == 0:
            raise ValueError("No voltage/frequency points specified.")

        self.daq = zhinst.core.ziDAQServer(
            self.server_host, int(self.server_port), api_level=6
        )
        self.daq.connectDevice(self.zur_id, interface=self.interface)
        self.sample_path = f"/{self.zur_id}/demods/{self.demod_index}/sample"
        self.daq.set(f"/{self.zur_id}/demods/{self.demod_index}/enable", 1)

        self.restore_amp = self._zurich_get_amp(self.osc_index)
        self.restore_freq = self._zurich_get_freq(self.osc_index)

    def execute(self):
        step_index = 0
        for v_idx, voltage in enumerate(self.voltages):
            for f_idx, frequency in enumerate(self.frequencies):
                if self.should_stop():
                    log.warning("Stop requested before step %d", step_index)
                    return

                self._set_drive(voltage, frequency)

                if self.delay > 0:
                    self._sleep_with_abort(self.delay)
                    if self.should_stop():
                        log.warning("Stop requested during delay at step %d", step_index)
                        return

                x, y, freq_meas = self._zurich_sample_read()
                amp_meas = self._zurich_get_amp(self.osc_index)
                freq_readback = self._zurich_get_freq(self.osc_index)

                data = {
                    "step_index": step_index,
                    "voltage_index": v_idx,
                    "frequency_index": f_idx,
                    "utc": time.time(),
                    "voltage_set": float(voltage),
                    "frequency_set": float(frequency),
                    "voltage_readback": float(amp_meas),
                    "frequency_readback": float(freq_readback),
                    "frequency_measured": float(freq_meas),
                    "x": float(x),
                    "y": float(y),
                    "r": float(np.sqrt(x**2 + y**2)),
                    "phase_deg": float(np.degrees(np.arctan2(y, x))),
                }

                self.emit("results", data)
                step_index += 1
                self.emit("progress", 100.0 * step_index / max(1, self.total_steps))

        self.emit("progress", 100.0)

    def shutdown(self):
        log.info("Restoring original drive settings")
        try:
            if self.restore_amp is not None and self.restore_freq is not None:
                self._set_drive(self.restore_amp, self.restore_freq)
        except Exception:
            log.exception("Unable to restore original drive settings")
        log.info("Finished")

    def check_parameters(self):
        params = self.parameter_objects()

        def ensure_set(name):
            if params[name].value is None:
                raise NameError(f"Missing value for '{name}'")

        for required in [
            "drive_voltages",
            "drive_frequencies",
            "delay",
            "zur_id",
            "osc_num",
            "demod_num",
            "server_host",
            "server_port",
            "interface",
        ]:
            ensure_set(required)

        if self.delay < 0:
            raise ValueError("delay must be >= 0")
        if self.osc_num <= 0 or self.demod_num <= 0:
            raise ValueError("osc_num and demod_num are 1-based and must be > 0")

        voltages = self._parse_list(self.drive_voltages, "drive_voltages")
        frequencies = self._parse_list(self.drive_frequencies, "drive_frequencies")
        if any(v < 0 for v in voltages):
            raise ValueError("drive_voltages must be >= 0")
        if any(f <= 0 for f in frequencies):
            raise ValueError("drive_frequencies must be > 0")

    @staticmethod
    def _parse_list(values: object, name: str) -> List[float]:
        if values is None:
            raise ValueError(f"{name} is required (comma/space separated list).")
        if isinstance(values, (list, tuple)):
            return [float(v) for v in values]
        text = str(values).strip()
        if not text:
            raise ValueError(f"{name} is required (comma/space separated list).")
        cleaned = text.strip().strip("[]()")
        parts = [p for p in re.split(r"[,\s]+", cleaned) if p]
        if not parts:
            raise ValueError(f"{name} is required (comma/space separated list).")
        return [float(p) for p in parts]

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
        self.daq.setDouble(amp_path, amplitude)
        self.daq.setDouble(freq_path, frequency)
        self.daq.sync()

    def _sleep_with_abort(self, duration: float):
        end_time = time.time() + duration
        while time.time() < end_time:
            if self.should_stop():
                break
            time.sleep(min(0.1, end_time - time.time()))


class DriveFrequencyGridConsole(ManagedConsole):
    """ManagedConsole with None-valued CLI params stripped out."""

    def __init__(self, procedure_class):
        super().__init__(procedure_class=procedure_class)
        self.parameter_values = {
            k: v for k, v in self.parameter_values.items() if v is not None
        }


def main():
    app = DriveFrequencyGridConsole(procedure_class=DriveFrequencyGridProcedure)
    try:
        sys.exit(app.exec())
    except KeyboardInterrupt:
        app.abort()
        sys.exit(1)


if __name__ == "__main__":
    main()
