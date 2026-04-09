import logging
import re
import sys
import time
from typing import Dict, Tuple

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


class DualSidebandFrequencySweepProcedure(Procedure):
    MAX_SAFE_DRIVE = 10e-3
    CARRIER_DEMOD_INDEX = 0
    SIDEBAND1_SLOT_INDEX = 0
    SIDEBAND2_SLOT_INDEX = 1

    carrier_xbkg = FloatParameter(
        "Carrier X background before rotation", units="V", default=-2.16e-3
    )
    carrier_ybkg = FloatParameter(
        "Carrier Y background before rotation", units="V", default=0.7886e-3
    )
    sideband1_xbkg = FloatParameter(
        "Sideband 1 X background before rotation", units="V", default=27.189e-6
    )
    sideband1_ybkg = FloatParameter(
        "Sideband 1 Y background before rotation", units="V", default=-12.67e-6
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

    carrier_drive = FloatParameter("Carrier drive", units="V", default=0)
    initial_carrier_frequency = FloatParameter(
        "Initial carrier frequency",
        units="Hz",
        default=800,
        decimals=10,
        ui_class=HighPrecisionScientificInput,
    )
    sideband1_drive = FloatParameter("Sideband 1 drive", units="V", default=3e-3)
    sideband2_drive = FloatParameter("Sideband 2 drive", units="V", default=1.71e-3)
    fixed_sideband1_diff_target = FloatParameter(
        "Fixed target for fc-fm1",
        units="Hz",
        default=645.702,
        decimals=10,
        ui_class=HighPrecisionScientificInput,
    )

    Q_guess = FloatParameter("Q guess", units="unitless")
    resonance_pt = FloatParameter(
        "Resonance guess for fc+fm2 (Hz)",
        default=1320.93209,
        units="Hz",
        decimals=10,
        ui_class=HighPrecisionScientificInput,
    )
    num_points = IntegerParameter("Number of points", default=21)
    reverse = BooleanParameter("Reverse sweep", default=False)
    initial_delay = FloatParameter("Initial delay (s)", units="s", default=1000.0)
    delay = FloatParameter("Delay (s)", units="s", default=600.0)
    file_prefix = Parameter("File prefix", default="dual_sideband_frequency_sweep")

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
        "initial_carrier_frequency",
        "sideband1_drive",
        "sideband2_drive",
        "fixed_sideband1_diff_target",
        "Q_guess",
        "resonance_pt",
        "num_points",
        "reverse",
        "initial_delay",
        "delay",
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
        "f_sideband1_osc_set",
        "f_sideband1_osc_readback",
        "f_sideband2_osc_set",
        "f_sideband2_osc_readback",
        "f_demod_sideband1_diff_set",
        "f_demod_sideband1_diff_readback",
        "f_demod_sideband2_sum_set",
        "f_demod_sideband2_sum_readback",
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
    ]

    def startup(self):
        log.info("Starting dual sideband frequency sweep")
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

        self.linewidth = self.resonance_pt / self.Q_guess
        self.sum_target_points = self._build_sum_target_points()
        self._validate_sweep_states()
        self.step_index = 0

        initial_state = self._initial_state()
        self._apply_state(initial_state)
        self._log_state(
            "Initial applied state",
            self.carrier_drive,
            self.sideband1_drive,
            self.sideband2_drive,
            initial_state["f_c"],
            initial_state["f_m1"],
            initial_state["f_m2"],
        )
        if not self._sleep_with_abort(float(self.initial_delay)):
            raise RuntimeError("Aborted during initial delay.")

    def execute(self):
        for i, sum_target in enumerate(self.sum_target_points):
            if self.should_stop():
                log.warning("Stop requested before frequency index %d", i)
                break

            state = self._state_for_sum_target(sum_target)
            self._apply_state(state)
            if not self._sleep_with_abort(float(self.delay)):
                break

            measurement = self._measure_once()
            measurement.update(
                {
                    "step_index": int(self.step_index),
                    "sweep_direction": 0 if self.reverse else 1,
                    "carrier_drive_set": float(self.carrier_drive),
                    "sideband1_drive_set": float(self.sideband1_drive),
                    "sideband2_drive_set": float(self.sideband2_drive),
                    "f_carrier_set": float(state["f_c"]),
                    "f_sideband1_osc_set": float(state["f_m1"]),
                    "f_sideband2_osc_set": float(state["f_m2"]),
                    "f_demod_sideband1_diff_set": float(
                        state["f_c"] - state["f_m1"]
                    ),
                    "f_demod_sideband2_sum_set": float(
                        state["f_c"] + state["f_m2"]
                    ),
                }
            )

            self.emit("results", measurement)
            self.emit("progress", 100 * (i + 1) / len(self.sum_target_points))
            self.step_index += 1

    def shutdown(self):
        if not hasattr(self, "daq"):
            return
        try:
            self._restore_state()
        except Exception:
            log.exception("Unable to restore original drive and frequency settings")

    def _validate_parameters(self) -> None:
        if self.num_points <= 0:
            raise ValueError("Number of points must be >= 1.")
        if self.Q_guess <= 0:
            raise ValueError("Q guess must be > 0.")
        if self.resonance_pt <= 0:
            raise ValueError("Resonance guess for fc+fm2 must be > 0.")
        if self.initial_carrier_frequency <= 0:
            raise ValueError("Initial carrier frequency must be > 0.")
        if self.delay < 0:
            raise ValueError("Delay must be >= 0.")
        if self.initial_delay < 0:
            raise ValueError("Initial delay must be >= 0.")
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
        self._validate_safe_amplitude("sideband1_drive", self.sideband1_drive)
        self._validate_safe_amplitude("sideband2_drive", self.sideband2_drive)

    def _build_sum_target_points(self) -> np.ndarray:
        n_total = int(self.num_points)
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

        points = np.concatenate([left_pts, inner_pts, right_pts])
        if self.reverse:
            points = points[::-1]
        return points

    def _state_for_sum_target(self, sum_target: float) -> Dict[str, float]:
        f_c = 0.5 * (float(sum_target) + float(self.fixed_sideband1_diff_target))
        f_m1 = f_c - float(self.fixed_sideband1_diff_target)
        f_m2 = float(sum_target) - f_c
        self._validate_frequency_state(f_c, f_m1, f_m2, context=f"sum target {sum_target:.10f}")
        return {"f_c": float(f_c), "f_m1": float(f_m1), "f_m2": float(f_m2)}

    def _initial_state(self) -> Dict[str, float]:
        f_c = float(self.initial_carrier_frequency)
        f_m1 = f_c - float(self.fixed_sideband1_diff_target)
        f_m2 = float(self.resonance_pt) - f_c
        self._validate_frequency_state(
            f_c,
            f_m1,
            f_m2,
            context="initial startup state",
        )
        return {"f_c": float(f_c), "f_m1": float(f_m1), "f_m2": float(f_m2)}

    def _validate_sweep_states(self) -> None:
        for sum_target in self.sum_target_points:
            self._state_for_sum_target(float(sum_target))

    def _validate_frequency_state(
        self,
        f_c: float,
        f_m1: float,
        f_m2: float,
        context: str,
    ) -> None:
        if f_c <= 0:
            raise ValueError(f"{context} makes the carrier frequency non-positive.")
        if f_m1 < 0:
            raise ValueError(f"{context} makes the sideband 1 oscillator frequency negative.")
        if f_m2 < 0:
            raise ValueError(f"{context} makes the sideband 2 oscillator frequency negative.")

    def _apply_state(self, state: Dict[str, float]) -> None:
        self._validate_safe_amplitude("carrier_drive", self.carrier_drive)
        self._validate_safe_amplitude("sideband1_drive", self.sideband1_drive)
        self._validate_safe_amplitude("sideband2_drive", self.sideband2_drive)
        self.daq.setDouble(self._carrier_amp_path(), float(self.carrier_drive))
        self.daq.setDouble(self._sideband_amp_path(self.SIDEBAND1_SLOT_INDEX), float(self.sideband1_drive))
        self.daq.setDouble(self._sideband_amp_path(self.SIDEBAND2_SLOT_INDEX), float(self.sideband2_drive))
        self.daq.setDouble(self._freq_path(self.carrier_osc_index), float(state["f_c"]))
        self.daq.setDouble(self._freq_path(self.sideband1_osc_index), float(state["f_m1"]))
        self.daq.setDouble(self._freq_path(self.sideband2_osc_index), float(state["f_m2"]))
        self.daq.sync()

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
            log.error(
                "Skipping unsafe sideband 1 restore amplitude %.9g V",
                self.restore_sideband1_amp,
            )
        if self._is_safe_amplitude(self.restore_sideband2_amp):
            self.daq.setDouble(
                self._sideband_amp_path(self.SIDEBAND2_SLOT_INDEX),
                float(self.restore_sideband2_amp),
            )
        else:
            log.error(
                "Skipping unsafe sideband 2 restore amplitude %.9g V",
                self.restore_sideband2_amp,
            )
        self.daq.setDouble(self._freq_path(self.carrier_osc_index), float(self.restore_carrier_freq))
        self.daq.setDouble(self._freq_path(self.sideband1_osc_index), float(self.restore_sideband1_freq))
        self.daq.setDouble(self._freq_path(self.sideband2_osc_index), float(self.restore_sideband2_freq))
        self.daq.sync()
        self._log_state(
            "Restored state",
            self._get_carrier_amp(),
            self._get_sideband_amp(self.SIDEBAND1_SLOT_INDEX),
            self._get_sideband_amp(self.SIDEBAND2_SLOT_INDEX),
            self._get_freq(self.carrier_osc_index),
            self._get_freq(self.sideband1_osc_index),
            self._get_freq(self.sideband2_osc_index),
        )

    def _validate_restore_state(self) -> None:
        self._validate_safe_amplitude("restore_carrier_amp", self.restore_carrier_amp)
        self._validate_safe_amplitude("restore_sideband1_amp", self.restore_sideband1_amp)
        self._validate_safe_amplitude("restore_sideband2_amp", self.restore_sideband2_amp)

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

        carrier_freq_readback = self._get_freq(self.carrier_osc_index)
        sideband1_freq_readback = self._get_freq(self.sideband1_osc_index)
        sideband2_freq_readback = self._get_freq(self.sideband2_osc_index)

        return {
            "utc": time.time(),
            "carrier_drive_readback": float(self._get_carrier_amp()),
            "sideband1_drive_readback": float(self._get_sideband_amp(self.SIDEBAND1_SLOT_INDEX)),
            "sideband2_drive_readback": float(self._get_sideband_amp(self.SIDEBAND2_SLOT_INDEX)),
            "f_carrier_readback": float(carrier_freq_readback),
            "f_sideband1_osc_readback": float(sideband1_freq_readback),
            "f_sideband2_osc_readback": float(sideband2_freq_readback),
            "f_demod_sideband1_diff_readback": float(
                carrier_freq_readback - sideband1_freq_readback
            ),
            "f_demod_sideband2_sum_readback": float(
                carrier_freq_readback + sideband2_freq_readback
            ),
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

    def _sample_read(self, sample_path: str):
        resp = self.daq.getSample(sample_path)
        return resp["x"][0], resp["y"][0], resp["frequency"][0]

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
                f"{label}={amplitude:.9g} V exceeds the hard safety limit of "
                f"{self.MAX_SAFE_DRIVE:.9g} V."
            )

    def _is_safe_amplitude(self, amplitude: float) -> bool:
        value = float(amplitude)
        return 0 <= value <= self.MAX_SAFE_DRIVE

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


class DualSidebandFrequencySweepWindow(ManagedDockWindow):
    def __init__(self):
        carrier_plot = PlotWidget(
            name="Carrier Response",
            columns=DualSidebandFrequencySweepProcedure.DATA_COLUMNS,
            x_axis="f_demod_sideband2_sum_readback",
            y_axis="carrier_R",
        )
        sideband1_plot = PlotWidget(
            name="Sideband 1 Response",
            columns=DualSidebandFrequencySweepProcedure.DATA_COLUMNS,
            x_axis="f_demod_sideband2_sum_readback",
            y_axis="sideband1_R",
        )
        sideband2_plot = PlotWidget(
            name="Sideband 2 Response",
            columns=DualSidebandFrequencySweepProcedure.DATA_COLUMNS,
            x_axis="f_demod_sideband2_sum_readback",
            y_axis="sideband2_R",
        )
        carrier_phase_plot = PlotWidget(
            name="Carrier Phase",
            columns=DualSidebandFrequencySweepProcedure.DATA_COLUMNS,
            x_axis="f_demod_sideband2_sum_readback",
            y_axis="carrier_phase",
        )
        sideband1_phase_plot = PlotWidget(
            name="Sideband 1 Phase",
            columns=DualSidebandFrequencySweepProcedure.DATA_COLUMNS,
            x_axis="f_demod_sideband2_sum_readback",
            y_axis="sideband1_phase",
        )
        sideband2_phase_plot = PlotWidget(
            name="Sideband 2 Phase",
            columns=DualSidebandFrequencySweepProcedure.DATA_COLUMNS,
            x_axis="f_demod_sideband2_sum_readback",
            y_axis="sideband2_phase",
        )

        super().__init__(
            procedure_class=DualSidebandFrequencySweepProcedure,
            inputs=DualSidebandFrequencySweepProcedure.PARAMETERS,
            displays=DualSidebandFrequencySweepProcedure.PARAMETERS,
            x_axis=["f_demod_sideband2_sum_readback"],
            y_axis=[
                "carrier_R",
                "sideband1_R",
                "sideband2_R",
                "carrier_phase",
                "sideband1_phase",
                "sideband2_phase",
            ],
            widget_list=(
                carrier_plot,
                sideband1_plot,
                sideband2_plot,
                carrier_phase_plot,
                sideband1_phase_plot,
                sideband2_phase_plot,
            ),
            inputs_in_scrollarea=True,
        )
        self.setWindowTitle("Zurich Dual Sideband Frequency Sweep")
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
    window = DualSidebandFrequencySweepWindow()
    window.show()
    sys.exit(app.exec_())
