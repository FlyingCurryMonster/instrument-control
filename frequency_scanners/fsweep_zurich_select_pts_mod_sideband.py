import logging
import re
import sys
import time

import numpy as np
import zhinst.core
from pymeasure.display.inputs import ScientificInput
from pymeasure.display.Qt import QtWidgets
from pymeasure.display.windows import ManagedWindow
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


class zurich_sideband_fsweep(Procedure):
    Q_guess = FloatParameter("Q guess", units="unitless")
    resonance_pt = FloatParameter(
        "Resonance guess (Hz)",
        decimals=15,
        ui_class=HighPrecisionScientificInput,
    )
    n_pts = IntegerParameter("Number of points", default=21)
    delay = FloatParameter("Delay (s)", default=1)
    reverse = BooleanParameter("Reverse sweep", default=False)

    carrier_osc_num_input = IntegerParameter("Carrier oscillator number", default=2)
    carrier_demod_num_input = IntegerParameter("Carrier demodulator number", default=1)
    mod_osc_num_input = IntegerParameter("Mod oscillator number", default=3)
    mod_demod_num_input = IntegerParameter("Mod demodulator number", default=3)
    zur_id = Parameter("Zurich addr.", default="dev4934")
    carrier_drive_amp = FloatParameter("Carrier drive amplitude (V)")
    fixed_sideband_freq = FloatParameter(
        "Fixed sideband demod frequency (Hz)",
        decimals=10,
        ui_class=HighPrecisionScientificInput,
    )

    comments = Parameter("Comments/Notes")

    params = [
        "Q_guess",
        "resonance_pt",
        "n_pts",
        "delay",
        "reverse",
        "zur_id",
        "carrier_osc_num_input",
        "carrier_demod_num_input",
        "mod_osc_num_input",
        "mod_demod_num_input",
        "carrier_drive_amp",
        "fixed_sideband_freq",
        "comments",
    ]

    DATA_COLUMNS = [
        "UTC",
        "timestamp",
        "direction",
        "f_c_set",
        "f_c_readback",
        "f_m_set",
        "f_m_readback",
        "f_sum_set",
        "f_sum_readback",
        "carrier_drive_amp",
        "carrier_demod_freq",
        "carrier_R",
        "carrier_phase",
        "carrier_X",
        "carrier_Y",
        "mod_demod_freq",
        "mod_R",
        "mod_phase",
        "mod_X",
        "mod_Y",
    ]

    def startup(self):
        log.info("Starting Zurich carrier/sideband frequency sweep")
        log.info("Carrier resonance guess at %s Hz", self.resonance_pt)

        self.carrier_osc_index = self.carrier_osc_num_input - 1
        self.carrier_demod_index = self.carrier_demod_num_input - 1
        self.mod_osc_index = self.mod_osc_num_input - 1
        self.mod_demod_index = self.mod_demod_num_input - 1

        self._validate_indices()

        zurich_ip_address = "192.168.77.26"
        port = 8004
        interface = "PCIe"
        self.daq = zhinst.core.ziDAQServer(zurich_ip_address, port, 6)
        self.daq.connectDevice(self.zur_id, interface=interface)

        self.daq.set(f"/{self.zur_id}/demods/{self.carrier_demod_index}/enable", 1)
        self.daq.set(f"/{self.zur_id}/demods/{self.mod_demod_index}/enable", 1)

        self.restore_carrier_freq = self._zurich_get_freq(self.carrier_osc_index)
        self.restore_mod_freq = self._zurich_get_freq(self.mod_osc_index)
        self.restore_carrier_amp = self._zurich_get_amp(self.carrier_osc_index)

        self.linewidth = self.resonance_pt / self.Q_guess
        self.freq = self._build_frequency_points()
        self.mod_freq = self.fixed_sideband_freq - self.freq
        self._validate_mod_frequency_range()

        log.info("Carrier frequency points for sweep: %s", self.freq)
        log.info("Modulation frequency points for sweep: %s", self.mod_freq)

        self.t_start = time.time()
        self._zurich_set_amp(self.carrier_osc_index, self.carrier_drive_amp)
        self._set_frequencies(self.freq[0], self.mod_freq[0])

    def execute(self):
        for i, f_c in enumerate(self.freq):
            f_m = self.fixed_sideband_freq - f_c
            self._set_frequencies(f_c, f_m)
            time.sleep(self.delay)

            utc_time = time.time()
            ts = utc_time - self.t_start

            carrier_x, carrier_y, carrier_demod_freq = self._zurich_sample_read(
                self.carrier_demod_index
            )
            mod_x, mod_y, mod_demod_freq = self._zurich_sample_read(self.mod_demod_index)
            carrier_freq_readback = self._zurich_get_freq(self.carrier_osc_index)
            mod_freq_readback = self._zurich_get_freq(self.mod_osc_index)

            data = {
                "UTC": utc_time,
                "timestamp": ts,
                "direction": 0 if self.reverse else 1,
                "f_c_set": float(f_c),
                "f_c_readback": float(carrier_freq_readback),
                "f_m_set": float(f_m),
                "f_m_readback": float(mod_freq_readback),
                "f_sum_set": float(self.fixed_sideband_freq),
                "f_sum_readback": float(carrier_freq_readback + mod_freq_readback),
                "carrier_drive_amp": float(self._zurich_get_amp(self.carrier_osc_index)),
                "carrier_demod_freq": float(carrier_demod_freq),
                "carrier_R": float(np.hypot(carrier_x, carrier_y)),
                "carrier_phase": float(np.degrees(np.arctan2(carrier_y, carrier_x))),
                "carrier_X": float(carrier_x),
                "carrier_Y": float(carrier_y),
                "mod_demod_freq": float(mod_demod_freq),
                "mod_R": float(np.hypot(mod_x, mod_y)),
                "mod_phase": float(np.degrees(np.arctan2(mod_y, mod_x))),
                "mod_X": float(mod_x),
                "mod_Y": float(mod_y),
            }

            self.emit("results", data)
            self.emit("progress", 100 * (i + 1) / len(self.freq))
            if self.should_stop():
                log.warning("Caught the stop flag in the procedure")
                break

    def shutdown(self):
        if not hasattr(self, "daq"):
            return

        try:
            if hasattr(self, "restore_carrier_amp"):
                self._zurich_set_amp(self.carrier_osc_index, self.restore_carrier_amp)
            if hasattr(self, "restore_carrier_freq") and hasattr(self, "restore_mod_freq"):
                self._set_frequencies(self.restore_carrier_freq, self.restore_mod_freq)
        except Exception:
            log.exception("Unable to restore original Zurich settings")

    def _build_frequency_points(self):
        n_total = int(self.n_pts)
        n_inner = (n_total + 1) // 2
        n_outside = n_total - n_inner
        n_left = n_outside // 2
        n_right = n_outside - n_left

        left_pts = np.linspace(
            self.resonance_pt - 3 * self.linewidth,
            self.resonance_pt - self.linewidth,
            n_left,
            endpoint=False,
        )
        inner_pts = np.linspace(
            self.resonance_pt - self.linewidth,
            self.resonance_pt + self.linewidth,
            n_inner,
            endpoint=False,
        )
        right_pts = np.linspace(
            self.resonance_pt + self.linewidth,
            self.resonance_pt + 3 * self.linewidth,
            n_right,
            endpoint=True,
        )

        freq = np.concatenate([left_pts, inner_pts, right_pts])
        if self.reverse:
            freq = freq[::-1]
        return freq

    def _validate_indices(self):
        for label, value in (
            ("carrier oscillator", self.carrier_osc_num_input),
            ("carrier demodulator", self.carrier_demod_num_input),
            ("mod oscillator", self.mod_osc_num_input),
            ("mod demodulator", self.mod_demod_num_input),
        ):
            if int(value) < 1:
                raise ValueError(f"{label} numbers must be 1-based positive integers")

    def _validate_mod_frequency_range(self):
        if np.any(self.mod_freq < 0):
            raise ValueError(
                "The requested fixed sideband frequency makes the modulation "
                "oscillator frequency negative for part of the sweep."
            )

    def _zurich_sample_read(self, demod_index):
        demod_path = f"/{self.zur_id}/demods/{demod_index}/sample"
        resp = self.daq.getSample(demod_path)
        return resp["x"][0], resp["y"][0], resp["frequency"][0]

    def _zurich_get_freq(self, osc_index):
        osc_path = f"/{self.zur_id}/oscs/{osc_index}/freq"
        return self.daq.getDouble(osc_path)

    def _zurich_set_freq(self, osc_index, frequency):
        osc_path = f"/{self.zur_id}/oscs/{osc_index}/freq"
        self.daq.setDouble(osc_path, float(frequency))

    def _set_frequencies(self, carrier_freq, mod_freq):
        self._zurich_set_freq(self.mod_osc_index, mod_freq)
        self._zurich_set_freq(self.carrier_osc_index, carrier_freq)

    def _zurich_get_amp(self, osc_index):
        amp_path = f"/{self.zur_id}/sigouts/0/amplitudes/{osc_index}"
        return self.daq.getDouble(amp_path)

    def _zurich_set_amp(self, osc_index, amplitude):
        amp_path = f"/{self.zur_id}/sigouts/0/amplitudes/{osc_index}"
        self.daq.setDouble(amp_path, amplitude)


class zurich_graph(ManagedWindow):
    def __init__(self):
        super().__init__(
            procedure_class=zurich_sideband_fsweep,
            inputs=zurich_sideband_fsweep.params,
            displays=zurich_sideband_fsweep.params,
            x_axis="f_c_readback",
            y_axis="carrier_X",
        )

        self.setWindowTitle("Zurich sideband frequency sweeper")
        self.directory = r"D:/Data/Fall25-Summer26/TO freq-sweeps"
        self.file_input.filename_fixed = False

    def queue(self):
        directory = self.directory
        filename = unique_filename(directory, prefix="zurich-sideband-")
        procedure = self.make_procedure()
        results = Results(procedure, filename)
        experiment = self.new_experiment(results)
        self.manager.queue(experiment)


if __name__ == "__main__":
    app = QtWidgets.QApplication([])
    window = zurich_graph()
    window.show()
    sys.exit(app.exec_())
