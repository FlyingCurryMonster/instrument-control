import logging
import re
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

import numpy as np
import zhinst.core
from pymeasure.display.inputs import ScientificInput
from pymeasure.display.Qt import QtWidgets
from pymeasure.display.widgets import PlotWidget
from pymeasure.display.windows.managed_dock_window import ManagedDockWindow
from pymeasure.experiment import (
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


class SidebandMonitorProcedure(Procedure):
    CARRIER_DEMOD_INDEX = 0
    SIDEBAND1_SLOT_INDEX = 0
    SIDEBAND2_SLOT_INDEX = 1

    measurement_time = FloatParameter("Measurement time", units="s", default=300)
    poll_interval = FloatParameter("Poll interval", units="s", default=1.0)
    file_prefix = Parameter("File prefix", default="sideband_monitor")

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
        "measurement_time",
        "poll_interval",
        "file_prefix",
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
        "t_rel",
        "utc",
        "carrier_drive",
        "sideband1_drive",
        "sideband2_drive",
        "f_carrier",
        "f_sideband1_osc",
        "f_sideband2_osc",
        "f_demod_sideband1_diff",
        "f_demod_sideband2_sum",
        "carrier_timeconstant",
        "sideband1_timeconstant",
        "sideband2_timeconstant",
        "carrier_sample_rate",
        "sideband1_sample_rate",
        "sideband2_sample_rate",
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
    ]

    def startup(self):
        log.info("Connecting to Zurich Instrument for passive sideband monitoring")
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
        self.sideband1_sample_path = f"/{self.zur_id}/demods/{self.sideband1_demod_index}/sample"
        self.sideband2_sample_path = f"/{self.zur_id}/demods/{self.sideband2_demod_index}/sample"
        self.clockbase = self.daq.getInt(f"/{self.zur_id}/clockbase")
        self.t_start = time.time()

        self._log_current_configuration()

    def execute(self):
        t_end = self.t_start + self.measurement_time
        self.daq.subscribe(self.carrier_sample_path)
        self.daq.subscribe(self.sideband1_sample_path)
        self.daq.subscribe(self.sideband2_sample_path)

        try:
            while time.time() < t_end and not self.should_stop():
                chunk = min(self.poll_interval, t_end - time.time())
                timeout_ms = int(1000 * (chunk + 0.1))
                poll = self.daq.poll(chunk, timeout_ms=timeout_ms, flags=0, flat=True)
                self._emit_from_poll(poll)
                elapsed = time.time() - self.t_start
                progress = 100.0 * elapsed / max(self.measurement_time, 1e-9)
                self.emit("progress", min(100.0, progress))
        finally:
            self.daq.unsubscribe(self.carrier_sample_path)
            self.daq.unsubscribe(self.sideband1_sample_path)
            self.daq.unsubscribe(self.sideband2_sample_path)
        self.emit("progress", 100)

    def _validate_parameters(self):
        if self.measurement_time <= 0:
            raise ValueError("measurement_time must be > 0")
        if self.poll_interval <= 0:
            raise ValueError("poll_interval must be > 0")
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

    def _emit_from_poll(self, poll_data: Dict):
        carrier_sample = self._extract_sample(
            poll_data, self.carrier_sample_path, self.CARRIER_DEMOD_INDEX
        )
        sideband1_sample = self._extract_sample(
            poll_data, self.sideband1_sample_path, self.sideband1_demod_index
        )
        sideband2_sample = self._extract_sample(
            poll_data, self.sideband2_sample_path, self.sideband2_demod_index
        )
        if not carrier_sample or not sideband1_sample or not sideband2_sample:
            return

        carrier_drive = self._get_carrier_amp()
        sideband1_drive = self._get_sideband_amp(self.SIDEBAND1_SLOT_INDEX)
        sideband2_drive = self._get_sideband_amp(self.SIDEBAND2_SLOT_INDEX)
        carrier_freq = self._get_freq(self.carrier_osc_index)
        sideband1_freq = self._get_freq(self.sideband1_osc_index)
        sideband2_freq = self._get_freq(self.sideband2_osc_index)
        carrier_tc = self._get_timeconstant(self.CARRIER_DEMOD_INDEX)
        sideband1_tc = self._get_timeconstant(self.sideband1_demod_index)
        sideband2_tc = self._get_timeconstant(self.sideband2_demod_index)
        carrier_rate = self._get_rate(self.CARRIER_DEMOD_INDEX)
        sideband1_rate = self._get_rate(self.sideband1_demod_index)
        sideband2_rate = self._get_rate(self.sideband2_demod_index)
        f_demod_sideband1_diff = carrier_freq - sideband1_freq
        f_demod_sideband2_sum = carrier_freq + sideband2_freq

        carrier_xs = np.asarray(carrier_sample.get("x", []), dtype=float)
        carrier_ys = np.asarray(carrier_sample.get("y", []), dtype=float)
        carrier_freqs = np.asarray(carrier_sample.get("frequency", []), dtype=float)
        carrier_timestamps = np.asarray(carrier_sample.get("timestamp", []), dtype=float)

        sideband1_xs = np.asarray(sideband1_sample.get("x", []), dtype=float)
        sideband1_ys = np.asarray(sideband1_sample.get("y", []), dtype=float)
        sideband1_freqs = np.asarray(sideband1_sample.get("frequency", []), dtype=float)

        sideband2_xs = np.asarray(sideband2_sample.get("x", []), dtype=float)
        sideband2_ys = np.asarray(sideband2_sample.get("y", []), dtype=float)
        sideband2_freqs = np.asarray(sideband2_sample.get("frequency", []), dtype=float)

        if carrier_timestamps.size:
            times = (carrier_timestamps - carrier_timestamps[0]) / float(self.clockbase)
        else:
            times = np.arange(len(carrier_xs), dtype=float) * 0.0

        n = min(
            len(carrier_xs),
            len(carrier_ys),
            len(sideband1_xs),
            len(sideband1_ys),
            len(sideband2_xs),
            len(sideband2_ys),
            len(times),
        )
        if n == 0:
            return

        chunk_t0 = time.time()
        for idx in range(n):
            carrier_x, carrier_y = self._subtract_background_and_rotate(
                carrier_xs[idx],
                carrier_ys[idx],
                self.carrier_xbkg,
                self.carrier_ybkg,
                self.carrier_phase_rotation,
            )
            sideband1_x, sideband1_y = self._subtract_background_and_rotate(
                sideband1_xs[idx],
                sideband1_ys[idx],
                self.sideband1_xbkg,
                self.sideband1_ybkg,
                self.sideband1_phase_rotation,
            )
            sideband2_x, sideband2_y = self._subtract_background_and_rotate(
                sideband2_xs[idx],
                sideband2_ys[idx],
                self.sideband2_xbkg,
                self.sideband2_ybkg,
                self.sideband2_phase_rotation,
            )

            carrier_demod_freq = carrier_freqs[idx] if idx < len(carrier_freqs) else np.nan
            sideband1_demod_freq = sideband1_freqs[idx] if idx < len(sideband1_freqs) else np.nan
            sideband2_demod_freq = sideband2_freqs[idx] if idx < len(sideband2_freqs) else np.nan

            sideband1_q, sideband1_f0, sideband1_tau = self._infer_metrics(
                sideband1_x,
                sideband1_y,
                f_demod_sideband1_diff,
                sideband1_drive,
                self.sideband1_k,
                self.sideband1_V0,
            )
            sideband2_q, sideband2_f0, sideband2_tau = self._infer_metrics(
                sideband2_x,
                sideband2_y,
                f_demod_sideband2_sum,
                sideband2_drive,
                self.sideband2_k,
                self.sideband2_V0,
            )

            t_rel = float((chunk_t0 - self.t_start) + times[idx])
            utc = self.t_start + t_rel
            data = {
                "t_rel": t_rel,
                "utc": float(utc),
                "carrier_drive": float(carrier_drive),
                "sideband1_drive": float(sideband1_drive),
                "sideband2_drive": float(sideband2_drive),
                "f_carrier": float(carrier_freq),
                "f_sideband1_osc": float(sideband1_freq),
                "f_sideband2_osc": float(sideband2_freq),
                "f_demod_sideband1_diff": float(f_demod_sideband1_diff),
                "f_demod_sideband2_sum": float(f_demod_sideband2_sum),
                "carrier_timeconstant": float(carrier_tc),
                "sideband1_timeconstant": float(sideband1_tc),
                "sideband2_timeconstant": float(sideband2_tc),
                "carrier_sample_rate": float(carrier_rate),
                "sideband1_sample_rate": float(sideband1_rate),
                "sideband2_sample_rate": float(sideband2_rate),
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
            self.emit("results", data)

    def _extract_sample(
        self, poll_data: Dict, sample_path: str, demod_index: int
    ) -> Optional[Dict[str, Iterable]]:
        if sample_path in poll_data:
            return poll_data[sample_path]
        try:
            return poll_data[self.zur_id]["demods"][str(demod_index)]["sample"]
        except Exception:
            return None

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

    def _infer_metrics(
        self,
        x: float,
        y: float,
        demod_freq: float,
        drive: float,
        k_constant: float,
        v0: float,
    ) -> Tuple[float, float, float]:
        if drive <= 0 or x == 0 or not np.isfinite(demod_freq):
            return np.nan, np.nan, np.nan
        k_effective = float(k_constant) * float(v0) / float(drive)
        q_infer = calculate_Q_infer(x, y, k_effective)
        if not np.isfinite(q_infer) or q_infer == 0:
            return float(q_infer), np.nan, np.nan
        f0_infer = calculate_f0_infer(x, y, demod_freq, k_effective)
        if not np.isfinite(f0_infer) or f0_infer == 0:
            return float(q_infer), float(f0_infer), np.nan
        tau_infer = q_infer / (np.pi * f0_infer)
        return float(q_infer), float(f0_infer), float(tau_infer)

    def _carrier_amp_path(self) -> str:
        return f"/{self.zur_id}/mods/0/carrier/amplitude"

    def _sideband_amp_path(self, slot_index: int) -> str:
        return f"/{self.zur_id}/mods/0/sidebands/{slot_index}/amplitude"

    def _freq_path(self, osc_index: int) -> str:
        return f"/{self.zur_id}/oscs/{osc_index}/freq"

    def _timeconstant_path(self, demod_index: int) -> str:
        return f"/{self.zur_id}/demods/{demod_index}/timeconstant"

    def _rate_path(self, demod_index: int) -> str:
        return f"/{self.zur_id}/demods/{demod_index}/rate"

    def _enable_path(self, demod_index: int) -> str:
        return f"/{self.zur_id}/demods/{demod_index}/enable"

    def _get_carrier_amp(self) -> float:
        return self.daq.getDouble(self._carrier_amp_path())

    def _get_sideband_amp(self, slot_index: int) -> float:
        return self.daq.getDouble(self._sideband_amp_path(slot_index))

    def _get_freq(self, osc_index: int) -> float:
        return self.daq.getDouble(self._freq_path(osc_index))

    def _get_timeconstant(self, demod_index: int) -> float:
        return self.daq.getDouble(self._timeconstant_path(demod_index))

    def _get_rate(self, demod_index: int) -> float:
        return self.daq.getDouble(self._rate_path(demod_index))

    def _get_enable(self, demod_index: int) -> int:
        return self.daq.getInt(self._enable_path(demod_index))

    def _log_current_configuration(self):
        log.info(
            "Current configuration: carrier_enable=%d sideband1_enable=%d sideband2_enable=%d "
            "carrier_drive=%.9g V sideband1_drive=%.9g V sideband2_drive=%.9g V "
            "f_carrier=%.10f Hz f_sideband1=%.10f Hz f_sideband2=%.10f Hz",
            self._get_enable(self.CARRIER_DEMOD_INDEX),
            self._get_enable(self.sideband1_demod_index),
            self._get_enable(self.sideband2_demod_index),
            self._get_carrier_amp(),
            self._get_sideband_amp(self.SIDEBAND1_SLOT_INDEX),
            self._get_sideband_amp(self.SIDEBAND2_SLOT_INDEX),
            self._get_freq(self.carrier_osc_index),
            self._get_freq(self.sideband1_osc_index),
            self._get_freq(self.sideband2_osc_index),
        )


class SidebandMonitorWindow(ManagedDockWindow):
    def __init__(self):
        carrier_plot = PlotWidget(
            name="Carrier X vs time",
            columns=SidebandMonitorProcedure.DATA_COLUMNS,
            x_axis="t_rel",
            y_axis="carrier_X",
        )
        sideband1_plot = PlotWidget(
            name="Sideband 1 R vs time",
            columns=SidebandMonitorProcedure.DATA_COLUMNS,
            x_axis="t_rel",
            y_axis="sideband1_R",
        )
        sideband2_plot = PlotWidget(
            name="Sideband 2 R vs time",
            columns=SidebandMonitorProcedure.DATA_COLUMNS,
            x_axis="t_rel",
            y_axis="sideband2_R",
        )
        q_plot = PlotWidget(
            name="Sideband Q",
            columns=SidebandMonitorProcedure.DATA_COLUMNS,
            x_axis="t_rel",
            y_axis="sideband1_Q_infer",
        )

        super().__init__(
            procedure_class=SidebandMonitorProcedure,
            inputs=SidebandMonitorProcedure.PARAMETERS,
            displays=SidebandMonitorProcedure.PARAMETERS,
            x_axis=["t_rel"],
            y_axis=["carrier_X", "sideband1_R", "sideband2_R", "sideband1_Q_infer", "sideband2_Q_infer"],
            widget_list=(carrier_plot, sideband1_plot, sideband2_plot, q_plot),
            inputs_in_scrollarea=True,
        )

        self.setWindowTitle("Zurich Sideband Monitor")
        self.filename_prefix = "sideband-monitor"
        self.directory = self._default_data_dir()

    def queue(self):
        filename = unique_filename(str(self.directory), prefix=f"{self.filename_prefix}_")
        procedure = self.make_procedure()
        results = Results(procedure, filename)
        experiment = self.new_experiment(results)
        self.manager.queue(experiment)

    @staticmethod
    def _default_data_dir() -> Path:
        preferred = Path(r"D:/Data/Fall25-Summer26/free-decay")
        if preferred.exists():
            return preferred
        fallback = Path(__file__).resolve().parent.parent / "data-files/free-decay"
        return fallback if fallback.exists() else Path.cwd()


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    window = SidebandMonitorWindow()
    window.show()
    sys.exit(app.exec_())
