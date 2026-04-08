import logging
import re
import sys
import time
from typing import Tuple

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


class SidebandFrequencySweepProcedure(Procedure):
    carrier_xbkg = FloatParameter(
        "Carrier X background before rotation", units="V", default=-2.16e-3
    )
    carrier_ybkg = FloatParameter(
        "Carrier Y background before rotation", units="V", default=0.7886e-3
    )
    sideband_xbkg = FloatParameter(
        "Sideband X background before rotation", units="V", default=27.189e-6
    )
    sideband_ybkg = FloatParameter(
        "Sideband Y background before rotation", units="V", default=-12.67e-6
    )
    carrier_phase_rotation = FloatParameter(
        "Carrier phase rotation", units="deg", default=-112.87
    )
    sideband_phase_rotation = FloatParameter(
        "Sideband phase rotation", units="deg", default=-98.367
    )

    carrier_drive = FloatParameter("Carrier drive", units="V", default=300e-6)
    carrier_frequency = FloatParameter(
        "Carrier frequency",
        units="Hz",
        default=1319.93647,
        decimals=10,
        ui_class=HighPrecisionScientificInput,
    )
    sideband_drive = FloatParameter("Sideband drive", units="V", default=267.6e-6)

    Q_guess = FloatParameter("Q guess", units="unitless")
    resonance_pt = FloatParameter(
        "Summed resonance guess (Hz)",
        units="Hz",
        decimals=10,
        ui_class=HighPrecisionScientificInput,
    )
    num_points = IntegerParameter("Number of points", default=21)
    reverse = BooleanParameter("Reverse sweep", default=False)
    delay = FloatParameter("Delay (s)", units="s", default=500.0)
    file_prefix = Parameter("File prefix", default="sideband_frequency_sweep")

    zur_id = Parameter("Zurich addr.", default="dev4934")
    carrier_osc_num = IntegerParameter("Carrier oscillator number", default=2)
    carrier_demod_num = IntegerParameter("Carrier demodulator number", default=1)
    sideband_osc_num = IntegerParameter("Sideband oscillator number", default=3)
    sideband_demod_num = IntegerParameter("Sideband demodulator number", default=3)
    server_host = Parameter("Server host", default="192.168.77.26")
    server_port = IntegerParameter("Server port", default=8004)
    interface = Parameter("Interface", default="PCIe")
    comments = Parameter("Comments/Notes", default="")

    PARAMETERS = [
        "carrier_xbkg",
        "carrier_ybkg",
        "sideband_xbkg",
        "sideband_ybkg",
        "carrier_phase_rotation",
        "sideband_phase_rotation",
        "carrier_drive",
        "carrier_frequency",
        "sideband_drive",
        "Q_guess",
        "resonance_pt",
        "num_points",
        "reverse",
        "delay",
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
        "sweep_direction",
        "utc",
        "carrier_drive_set",
        "carrier_drive_readback",
        "sideband_drive_set",
        "sideband_drive_readback",
        "f_carrier_set",
        "f_carrier_readback",
        "f_sideband_osc_set",
        "f_sideband_osc_readback",
        "f_demod_sideband_sum_set",
        "f_demod_sideband_sum_readback",
        "carrier_demod_freq",
        "sideband_demod_freq",
        "carrier_X",
        "carrier_Y",
        "carrier_R",
        "carrier_phase",
        "sideband_X",
        "sideband_Y",
        "sideband_R",
        "sideband_phase",
    ]

    def startup(self):
        log.info("Starting sideband frequency sweep with fixed carrier settings")
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

        self.restore_carrier_amp = self._zurich_get_carrier_amp()
        self.restore_sideband_amp = self._zurich_get_sideband_amp()
        self.restore_carrier_freq = self._zurich_get_freq(self.carrier_osc_index)
        self.restore_sideband_freq = self._zurich_get_freq(self.sideband_osc_index)

        self.sideband_resonance_pt = self.resonance_pt - self.carrier_frequency
        self.linewidth = self.resonance_pt / self.Q_guess
        self.sideband_freq_points = self._build_sideband_frequency_points()
        if np.any(self.sideband_freq_points < 0):
            raise ValueError(
                "The sideband frequency sweep contains negative frequencies. "
                "Adjust the resonance guess or Q guess."
            )
        log.info(
            "Summed resonance guess %.10f Hz implies sideband center %.10f Hz",
            self.resonance_pt,
            self.sideband_resonance_pt,
        )
        self.step_index = 0

        self._apply_step(self.sideband_freq_points[0])

    def execute(self):
        for i, sideband_freq in enumerate(self.sideband_freq_points):
            if self.should_stop():
                log.warning("Stop requested before sideband frequency index %d", i)
                break

            self._apply_step(sideband_freq)
            if not self._sleep_with_abort(float(self.delay)):
                break

            measurement = self._measure_once()
            measurement.update(
                {
                    "step_index": int(self.step_index),
                    "sweep_direction": 0 if self.reverse else 1,
                    "carrier_drive_set": float(self.carrier_drive),
                    "sideband_drive_set": float(self.sideband_drive),
                    "f_carrier_set": float(self.carrier_frequency),
                    "f_sideband_osc_set": float(sideband_freq),
                    "f_demod_sideband_sum_set": float(
                        self.carrier_frequency + sideband_freq
                    ),
                }
            )

            self.emit("results", measurement)
            self.emit("progress", 100 * (i + 1) / len(self.sideband_freq_points))
            self.step_index += 1

    def shutdown(self):
        if not hasattr(self, "daq"):
            return
        try:
            self._restore_state()
            log.info("Restored original drive and frequency settings")
        except Exception:
            log.exception("Unable to restore original drive and frequency settings")

    def _validate_parameters(self) -> None:
        if self.num_points <= 0:
            raise ValueError("Number of points must be >= 1.")
        if self.carrier_drive < 0 or self.sideband_drive < 0:
            raise ValueError("Drive voltages must be >= 0.")
        if self.carrier_frequency <= 0:
            raise ValueError("Carrier frequency must be > 0.")
        if self.Q_guess <= 0:
            raise ValueError("Q guess must be > 0.")
        if self.resonance_pt <= 0:
            raise ValueError("Summed resonance guess must be > 0.")
        if self.resonance_pt <= self.carrier_frequency:
            raise ValueError(
                "Summed resonance guess must be greater than the carrier frequency "
                "so the implied sideband center is positive."
            )
        if self.delay < 0:
            raise ValueError("Delay must be >= 0.")
        if min(
            self.carrier_osc_num,
            self.carrier_demod_num,
            self.sideband_osc_num,
            self.sideband_demod_num,
        ) <= 0:
            raise ValueError("Oscillator and demodulator numbers are 1-based and must be > 0.")

    def _build_sideband_frequency_points(self) -> np.ndarray:
        n_total = int(self.num_points)
        n_inner = (n_total + 1) // 2
        n_outside = n_total - n_inner
        n_left = n_outside // 2
        n_right = n_outside - n_left

        left_pts = np.linspace(
            self.sideband_resonance_pt - 3 * self.linewidth,
            self.sideband_resonance_pt - self.linewidth,
            n_left,
            endpoint=False,
        )
        inner_pts = np.linspace(
            self.sideband_resonance_pt - self.linewidth,
            self.sideband_resonance_pt + self.linewidth,
            n_inner,
            endpoint=False,
        )
        right_pts = np.linspace(
            self.sideband_resonance_pt + self.linewidth,
            self.sideband_resonance_pt + 3 * self.linewidth,
            n_right,
            endpoint=True,
        )

        points = np.concatenate([left_pts, inner_pts, right_pts])
        if self.reverse:
            points = points[::-1]
        return points

    def _apply_step(self, sideband_freq: float) -> None:
        carrier_amp_path = f"/{self.zur_id}/mods/0/carrier/amplitude"
        sideband_amp_path = f"/{self.zur_id}/mods/0/sidebands/1/amplitude"
        carrier_freq_path = f"/{self.zur_id}/oscs/{self.carrier_osc_index}/freq"
        sideband_freq_path = f"/{self.zur_id}/oscs/{self.sideband_osc_index}/freq"

        self.daq.setDouble(carrier_amp_path, float(self.carrier_drive))
        self.daq.setDouble(sideband_amp_path, float(self.sideband_drive))
        self.daq.setDouble(carrier_freq_path, float(self.carrier_frequency))
        self.daq.setDouble(sideband_freq_path, float(sideband_freq))
        self.daq.sync()

    def _restore_state(self) -> None:
        carrier_amp_path = f"/{self.zur_id}/mods/0/carrier/amplitude"
        sideband_amp_path = f"/{self.zur_id}/mods/0/sidebands/1/amplitude"
        carrier_freq_path = f"/{self.zur_id}/oscs/{self.carrier_osc_index}/freq"
        sideband_freq_path = f"/{self.zur_id}/oscs/{self.sideband_osc_index}/freq"

        self.daq.setDouble(carrier_amp_path, float(self.restore_carrier_amp))
        self.daq.setDouble(sideband_amp_path, float(self.restore_sideband_amp))
        self.daq.setDouble(carrier_freq_path, float(self.restore_carrier_freq))
        self.daq.setDouble(sideband_freq_path, float(self.restore_sideband_freq))
        self.daq.sync()

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

        carrier_drive_readback = self._zurich_get_carrier_amp()
        sideband_drive_readback = self._zurich_get_sideband_amp()
        carrier_freq_readback = self._zurich_get_freq(self.carrier_osc_index)
        sideband_freq_readback = self._zurich_get_freq(self.sideband_osc_index)

        return {
            "utc": time.time(),
            "carrier_drive_readback": float(carrier_drive_readback),
            "sideband_drive_readback": float(sideband_drive_readback),
            "f_carrier_readback": float(carrier_freq_readback),
            "f_sideband_osc_readback": float(sideband_freq_readback),
            "f_demod_sideband_sum_readback": float(
                carrier_freq_readback + sideband_freq_readback
            ),
            "carrier_demod_freq": float(carrier_demod_freq),
            "sideband_demod_freq": float(sideband_demod_freq),
            "carrier_X": float(carrier_x),
            "carrier_Y": float(carrier_y),
            "carrier_R": float(np.hypot(carrier_x, carrier_y)),
            "carrier_phase": float(np.degrees(np.arctan2(carrier_y, carrier_x))),
            "sideband_X": float(sideband_x),
            "sideband_Y": float(sideband_y),
            "sideband_R": float(np.hypot(sideband_x, sideband_y)),
            "sideband_phase": float(np.degrees(np.arctan2(sideband_y, sideband_x))),
        }

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

    def _zurich_sample_read(self, sample_path: str):
        resp = self.daq.getSample(sample_path)
        return resp["x"][0], resp["y"][0], resp["frequency"][0]

    def _zurich_get_carrier_amp(self) -> float:
        amp_path = f"/{self.zur_id}/mods/0/carrier/amplitude"
        return self.daq.getDouble(amp_path)

    def _zurich_get_sideband_amp(self) -> float:
        amp_path = f"/{self.zur_id}/mods/0/sidebands/1/amplitude"
        return self.daq.getDouble(amp_path)

    def _zurich_get_freq(self, osc_index: int) -> float:
        osc_path = f"/{self.zur_id}/oscs/{osc_index}/freq"
        return self.daq.getDouble(osc_path)


class SidebandFrequencySweepWindow(ManagedDockWindow):
    def __init__(self):
        carrier_response_plot = PlotWidget(
            name="Carrier Response",
            columns=SidebandFrequencySweepProcedure.DATA_COLUMNS,
            x_axis="f_sideband_osc_readback",
            y_axis="carrier_R",
        )
        sideband_response_plot = PlotWidget(
            name="Sideband Response",
            columns=SidebandFrequencySweepProcedure.DATA_COLUMNS,
            x_axis="f_sideband_osc_readback",
            y_axis="sideband_R",
        )
        carrier_phase_plot = PlotWidget(
            name="Carrier Phase",
            columns=SidebandFrequencySweepProcedure.DATA_COLUMNS,
            x_axis="f_sideband_osc_readback",
            y_axis="carrier_phase",
        )
        sideband_phase_plot = PlotWidget(
            name="Sideband Phase",
            columns=SidebandFrequencySweepProcedure.DATA_COLUMNS,
            x_axis="f_sideband_osc_readback",
            y_axis="sideband_phase",
        )

        super().__init__(
            procedure_class=SidebandFrequencySweepProcedure,
            inputs=SidebandFrequencySweepProcedure.PARAMETERS,
            displays=SidebandFrequencySweepProcedure.PARAMETERS,
            x_axis=["f_sideband_osc_readback"],
            y_axis=["carrier_R", "sideband_R", "carrier_phase", "sideband_phase"],
            widget_list=(
                carrier_response_plot,
                sideband_response_plot,
                carrier_phase_plot,
                sideband_phase_plot,
            ),
            inputs_in_scrollarea=True,
        )
        self.setWindowTitle("Zurich Sideband Frequency Sweep")
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
    window = SidebandFrequencySweepWindow()
    window.show()
    sys.exit(app.exec_())
