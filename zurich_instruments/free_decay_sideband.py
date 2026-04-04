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


class FreeDecaySidebandProcedure(Procedure):
    """Drop the carrier drive to zero and record carrier/sideband demod responses."""

    iterations = IntegerParameter("Loop Iterations", default=1)
    delay_before_drop = FloatParameter("Initial delay", units="s", default=0.2)
    measurement_time = FloatParameter("Measurement time", units="s", default=300)
    ring_up_time = FloatParameter("Ring-up time", units="s", default=300)
    poll_interval = FloatParameter("Poll interval", units="s", default=1.0)
    carrier_drive_final = FloatParameter(
        "Carrier drive final", units="V", default=0.0
    )

    use_current_sideband_drive = BooleanParameter(
        "Use current sideband amplitude/frequency", default=True
    )
    initial_sideband_voltage = FloatParameter("Sideband drive amplitude", units="V")
    initial_sideband_frequency = FloatParameter(
        "Sideband drive frequency",
        units="Hz",
        decimals=10,
        ui_class=HighPrecisionScientificInput,
    )

    use_current_time_constants = BooleanParameter("Use current demod time constants", default=True)
    carrier_timeconstant = FloatParameter("Carrier time constant", units="s")
    sideband_timeconstant = FloatParameter("Sideband time constant", units="s")

    settle_after_set = FloatParameter("Wait after setting values", units="s", default=0.2)

    carrier_k = FloatParameter("Carrier k constant", units="1/V", default=3931072.9988525705)
    carrier_V0 = FloatParameter(
        "Carrier drive that k was obtained at", units="V", default=300e-6
    )
    sideband_k = FloatParameter("Sideband k constant", units="1/V", default=93260373.7208108)
    sideband_V0 = FloatParameter(
        "Sideband drive that k was obtained at", units="V", default=267.6e-6
    )

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
    carrier_phase_rotation = FloatParameter("Carrier phase rotation", units="deg", default=-112.87)
    sideband_phase_rotation = FloatParameter("Sideband phase rotation", units="deg", default=-98.367)

    zur_id = Parameter("Zurich addr.", default="dev4934")
    carrier_demod_num = IntegerParameter("Carrier demodulator number", default=1)
    sideband_demod_num = IntegerParameter("Sideband demodulator number", default=3)
    sideband_osc_num = IntegerParameter("Sideband oscillator number", default=3)
    server_host = Parameter("Server host", default="192.168.77.26")
    server_port = IntegerParameter("Server port", default=8004)
    interface = Parameter("Interface", default="PCIe")
    comments = Parameter("Comments/Notes", default="")

    PARAMETERS = [
        "iterations",
        "delay_before_drop",
        "measurement_time",
        "ring_up_time",
        "poll_interval",
        "carrier_drive_final",
        "use_current_sideband_drive",
        "initial_sideband_voltage",
        "initial_sideband_frequency",
        "use_current_time_constants",
        "carrier_timeconstant",
        "sideband_timeconstant",
        "settle_after_set",
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
        "zur_id",
        "carrier_demod_num",
        "sideband_demod_num",
        "sideband_osc_num",
        "server_host",
        "server_port",
        "interface",
        "comments",
    ]

    DATA_COLUMNS = [
        "iteration",
        "t_rel",
        "utc",
        "carrier_drive_initial",
        "carrier_drive_final",
        "sideband_drive_before_drop",
        "sideband_freq_before_drop",
        "carrier_timeconstant",
        "sideband_timeconstant",
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
        "carrier_Q_infer",
        "carrier_f0_infer",
        "carrier_tau_infer",
        "sideband_Q_infer",
        "sideband_f0_infer",
        "sideband_tau_infer",
    ]

    def startup(self):
        log.info("Connecting to Zurich Instrument for sideband free decay capture")

        self.restore_carrier_amp = None
        self.restore_sideband_amp = None
        self.restore_sideband_freq = None
        self.restore_carrier_tc = None
        self.restore_sideband_tc = None

        self.carrier_demod_index = self.carrier_demod_num - 1
        self.sideband_demod_index = self.sideband_demod_num - 1
        self.sideband_osc_index = self.sideband_osc_num - 1

        self.daq = zhinst.core.ziDAQServer(
            self.server_host, int(self.server_port), api_level=6
        )
        self.daq.connectDevice(self.zur_id, interface=self.interface)

        self.carrier_sample_path = f"/{self.zur_id}/demods/{self.carrier_demod_index}/sample"
        self.sideband_sample_path = f"/{self.zur_id}/demods/{self.sideband_demod_index}/sample"
        self.daq.set(f"/{self.zur_id}/demods/{self.carrier_demod_index}/enable", 1)
        self.daq.set(f"/{self.zur_id}/demods/{self.sideband_demod_index}/enable", 1)

        self.clockbase = self.daq.getInt(f"/{self.zur_id}/clockbase")

        self.restore_carrier_amp = self._zurich_get_carrier_amp()
        self.restore_sideband_amp = self._zurich_get_sideband_amp()
        self.restore_sideband_freq = self._zurich_get_freq(self.sideband_osc_index)
        self.restore_carrier_tc = self._zurich_get_timeconstant(self.carrier_demod_index)
        self.restore_sideband_tc = self._zurich_get_timeconstant(self.sideband_demod_index)

        self._validate_parameters()

        if self.use_current_sideband_drive:
            self.target_sideband_amp = self.restore_sideband_amp
            self.target_sideband_freq = self.restore_sideband_freq
            self.initial_sideband_voltage = self.target_sideband_amp
            self.initial_sideband_frequency = self.target_sideband_freq
        else:
            self.target_sideband_amp = float(self.initial_sideband_voltage)
            self.target_sideband_freq = float(self.initial_sideband_frequency)
            self._set_sideband_drive(
                amplitude=self.target_sideband_amp,
                frequency=self.target_sideband_freq,
            )

        if self.use_current_time_constants:
            self.active_carrier_tc = self.restore_carrier_tc
            self.active_sideband_tc = self.restore_sideband_tc
            self.carrier_timeconstant = self.active_carrier_tc
            self.sideband_timeconstant = self.active_sideband_tc
        else:
            self.active_carrier_tc = float(self.carrier_timeconstant)
            self.active_sideband_tc = float(self.sideband_timeconstant)
            self._set_timeconstant(self.carrier_demod_index, self.active_carrier_tc)
            self._set_timeconstant(self.sideband_demod_index, self.active_sideband_tc)

        changed_anything = (not self.use_current_sideband_drive) or (
            not self.use_current_time_constants
        )
        self.pre_drop_wait = self.settle_after_set if changed_anything else self.delay_before_drop

    def execute(self):
        for iteration in range(self.iterations):
            if self.should_stop():
                log.warning("Stop requested before iteration %d", iteration)
                break

            wait_time = self.pre_drop_wait if iteration == 0 else self.ring_up_time
            if wait_time > 0:
                self._sleep_with_abort(wait_time)
                if self.should_stop():
                    log.warning("Stop requested while waiting before iteration %d", iteration)
                    break

            pre_drop_carrier_amp = self._zurich_get_carrier_amp()
            pre_drop_sideband_amp = self._zurich_get_sideband_amp()
            pre_drop_sideband_freq = self._zurich_get_freq(self.sideband_osc_index)

            self._set_carrier_amp(self.carrier_drive_final)
            drop_time_utc = time.time()

            drop_device_ts = None
            try:
                drop_sample = self.daq.getSample(self.carrier_sample_path)
                ts_arr = np.asarray(drop_sample.get("timestamp", []), dtype=float)
                if ts_arr.size:
                    drop_device_ts = float(ts_arr[-1])
            except Exception:
                log.exception("Unable to read carrier demod timestamp at drop; continuing.")

            log.info("Starting decay %d/%d", iteration + 1, self.iterations)
            self._record_decay(
                iteration=iteration,
                drop_time_utc=drop_time_utc,
                drop_device_ts=drop_device_ts,
                carrier_drive_initial=pre_drop_carrier_amp,
                carrier_drive_final=self.carrier_drive_final,
                sideband_drive_before_drop=pre_drop_sideband_amp,
                sideband_freq_before_drop=pre_drop_sideband_freq,
            )

            progress = 100 * (iteration + 1) / self.iterations
            self.emit("progress", progress)

            if self.should_stop():
                log.warning("Stop requested after iteration %d", iteration)
                break

            if iteration < self.iterations - 1:
                self._restore_between_iterations()

        self.emit("progress", 100)

    def shutdown(self):
        log.info("Restoring original sideband free decay settings")
        try:
            self._restore_full_state()
        except Exception:
            log.exception("Unable to restore the original free decay settings.")
        log.info("Finished")

    def check_parameters(self):
        params = self.parameter_objects()

        def ensure_set(name):
            if params[name].value is None:
                raise NameError(f"Missing value for '{name}'")

        for required in [
            "iterations",
            "delay_before_drop",
            "measurement_time",
            "ring_up_time",
            "poll_interval",
            "carrier_drive_final",
            "use_current_sideband_drive",
            "use_current_time_constants",
            "settle_after_set",
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
            "zur_id",
            "carrier_demod_num",
            "sideband_demod_num",
            "sideband_osc_num",
            "server_host",
            "server_port",
            "interface",
        ]:
            ensure_set(required)

        if not self.use_current_sideband_drive:
            for name in ["initial_sideband_voltage", "initial_sideband_frequency"]:
                ensure_set(name)
        if not self.use_current_time_constants:
            for name in ["carrier_timeconstant", "sideband_timeconstant"]:
                ensure_set(name)

    def _validate_parameters(self):
        if self.iterations <= 0:
            raise ValueError("iterations must be > 0")
        if self.delay_before_drop < 0:
            raise ValueError("delay_before_drop must be >= 0")
        if self.measurement_time <= 0:
            raise ValueError("measurement_time must be > 0")
        if self.ring_up_time < 0:
            raise ValueError("ring_up_time must be >= 0")
        if self.poll_interval <= 0:
            raise ValueError("poll_interval must be > 0")
        if self.carrier_drive_final < 0:
            raise ValueError("carrier_drive_final must be >= 0")
        if self.settle_after_set < 0:
            raise ValueError("settle_after_set must be >= 0")
        if min(self.carrier_demod_num, self.sideband_demod_num, self.sideband_osc_num) <= 0:
            raise ValueError("Demodulator and oscillator numbers are 1-based and must be > 0.")
        if not self.use_current_sideband_drive:
            if self.initial_sideband_voltage is None or self.initial_sideband_voltage < 0:
                raise ValueError(
                    "initial_sideband_voltage must be >= 0 when not using current sideband drive."
                )
            if self.initial_sideband_frequency is None or self.initial_sideband_frequency <= 0:
                raise ValueError(
                    "initial_sideband_frequency must be > 0 when not using current sideband drive."
                )
        if not self.use_current_time_constants:
            if self.carrier_timeconstant is None or self.carrier_timeconstant <= 0:
                raise ValueError("carrier_timeconstant must be > 0 when not using current values.")
            if self.sideband_timeconstant is None or self.sideband_timeconstant <= 0:
                raise ValueError("sideband_timeconstant must be > 0 when not using current values.")

    def _zurich_get_carrier_amp(self) -> float:
        return self.daq.getDouble(f"/{self.zur_id}/mods/0/carrier/amplitude")

    def _zurich_get_sideband_amp(self) -> float:
        return self.daq.getDouble(f"/{self.zur_id}/mods/0/sidebands/1/amplitude")

    def _set_carrier_amp(self, amplitude: float):
        self.daq.setDouble(f"/{self.zur_id}/mods/0/carrier/amplitude", float(amplitude))
        self.daq.sync()

    def _set_sideband_drive(self, amplitude: float, frequency: float):
        self.daq.setDouble(f"/{self.zur_id}/mods/0/sidebands/1/amplitude", float(amplitude))
        self.daq.setDouble(f"/{self.zur_id}/oscs/{self.sideband_osc_index}/freq", float(frequency))
        self.daq.sync()

    def _zurich_get_freq(self, osc_num: int) -> float:
        return self.daq.getDouble(f"/{self.zur_id}/oscs/{osc_num}/freq")

    def _zurich_get_timeconstant(self, demod_index: int) -> float:
        return self.daq.getDouble(f"/{self.zur_id}/demods/{demod_index}/timeconstant")

    def _set_timeconstant(self, demod_index: int, value: float):
        self.daq.setDouble(f"/{self.zur_id}/demods/{demod_index}/timeconstant", float(value))

    def _restore_between_iterations(self):
        self._set_sideband_drive(
            amplitude=self.target_sideband_amp,
            frequency=self.target_sideband_freq,
        )
        self._set_carrier_amp(self.restore_carrier_amp)

    def _restore_full_state(self):
        if self.restore_carrier_amp is not None:
            self.daq.setDouble(
                f"/{self.zur_id}/mods/0/carrier/amplitude", float(self.restore_carrier_amp)
            )
        if self.restore_sideband_amp is not None:
            self.daq.setDouble(
                f"/{self.zur_id}/mods/0/sidebands/1/amplitude",
                float(self.restore_sideband_amp),
            )
        if self.restore_sideband_freq is not None:
            self.daq.setDouble(
                f"/{self.zur_id}/oscs/{self.sideband_osc_index}/freq",
                float(self.restore_sideband_freq),
            )
        if self.restore_carrier_tc is not None:
            self.daq.setDouble(
                f"/{self.zur_id}/demods/{self.carrier_demod_index}/timeconstant",
                float(self.restore_carrier_tc),
            )
        if self.restore_sideband_tc is not None:
            self.daq.setDouble(
                f"/{self.zur_id}/demods/{self.sideband_demod_index}/timeconstant",
                float(self.restore_sideband_tc),
            )
        self.daq.sync()

    def _record_decay(
        self,
        iteration: int,
        drop_time_utc: float,
        drop_device_ts: Optional[float],
        carrier_drive_initial: float,
        carrier_drive_final: float,
        sideband_drive_before_drop: float,
        sideband_freq_before_drop: float,
    ):
        t_start = time.time()
        t_end = t_start + self.measurement_time
        self.daq.subscribe(self.carrier_sample_path)
        self.daq.subscribe(self.sideband_sample_path)

        try:
            while time.time() < t_end and not self.should_stop():
                chunk = min(self.poll_interval, t_end - time.time())
                timeout_ms = int(1000 * (chunk + 0.1))
                poll = self.daq.poll(chunk, timeout_ms=timeout_ms, flags=0, flat=True)
                self._emit_from_poll(
                    poll_data=poll,
                    iteration=iteration,
                    drop_time_utc=drop_time_utc,
                    drop_device_ts=drop_device_ts,
                    carrier_drive_initial=carrier_drive_initial,
                    carrier_drive_final=carrier_drive_final,
                    sideband_drive_before_drop=sideband_drive_before_drop,
                    sideband_freq_before_drop=sideband_freq_before_drop,
                )
                iter_frac = min(1.0, (time.time() - t_start) / self.measurement_time)
                overall = 100.0 * (iteration + iter_frac) / max(1, self.iterations)
                self.emit("progress", overall)
        finally:
            self.daq.unsubscribe(self.carrier_sample_path)
            self.daq.unsubscribe(self.sideband_sample_path)

    def _emit_from_poll(
        self,
        poll_data: Dict,
        iteration: int,
        drop_time_utc: float,
        drop_device_ts: Optional[float],
        carrier_drive_initial: float,
        carrier_drive_final: float,
        sideband_drive_before_drop: float,
        sideband_freq_before_drop: float,
    ):
        carrier_sample = self._extract_sample(poll_data, self.carrier_sample_path, self.carrier_demod_index)
        sideband_sample = self._extract_sample(
            poll_data, self.sideband_sample_path, self.sideband_demod_index
        )
        if not carrier_sample or not sideband_sample:
            return

        carrier_xs = np.asarray(carrier_sample.get("x", []), dtype=float)
        carrier_ys = np.asarray(carrier_sample.get("y", []), dtype=float)
        carrier_freqs = np.asarray(carrier_sample.get("frequency", []), dtype=float)
        carrier_timestamps = np.asarray(carrier_sample.get("timestamp", []), dtype=float)

        sideband_xs = np.asarray(sideband_sample.get("x", []), dtype=float)
        sideband_ys = np.asarray(sideband_sample.get("y", []), dtype=float)
        sideband_freqs = np.asarray(sideband_sample.get("frequency", []), dtype=float)

        times = None
        if carrier_timestamps.size and drop_device_ts is not None:
            times = (carrier_timestamps - drop_device_ts) / float(self.clockbase)
        else:
            times = carrier_sample.get("time")
            if isinstance(times, dict):
                times = times.get("value")
            if times is None:
                if carrier_timestamps.size:
                    times = (carrier_timestamps - carrier_timestamps[0]) / float(self.clockbase)
                else:
                    times = np.arange(len(carrier_xs), dtype=float) * 0.0
            else:
                times = np.asarray(times, dtype=float)

        n = min(
            len(carrier_xs),
            len(carrier_ys),
            len(sideband_xs),
            len(sideband_ys),
            len(times),
        )
        if n == 0:
            return

        for idx in range(n):
            carrier_x, carrier_y = self._subtract_background_and_rotate(
                carrier_xs[idx],
                carrier_ys[idx],
                self.carrier_xbkg,
                self.carrier_ybkg,
                self.carrier_phase_rotation,
            )
            sideband_x, sideband_y = self._subtract_background_and_rotate(
                sideband_xs[idx],
                sideband_ys[idx],
                self.sideband_xbkg,
                self.sideband_ybkg,
                self.sideband_phase_rotation,
            )

            carrier_demod_freq = (
                carrier_freqs[idx] if idx < len(carrier_freqs) else np.nan
            )
            sideband_demod_freq = (
                sideband_freqs[idx] if idx < len(sideband_freqs) else np.nan
            )

            carrier_q, carrier_f0, carrier_tau = self._infer_metrics(
                x=carrier_x,
                y=carrier_y,
                demod_freq=carrier_demod_freq,
                drive_before_drop=carrier_drive_initial,
                k_constant=self.carrier_k,
                v0=self.carrier_V0,
            )
            sideband_q, sideband_f0, sideband_tau = self._infer_metrics(
                x=sideband_x,
                y=sideband_y,
                demod_freq=sideband_demod_freq,
                drive_before_drop=sideband_drive_before_drop,
                k_constant=self.sideband_k,
                v0=self.sideband_V0,
            )

            carrier_r = np.hypot(carrier_x, carrier_y)
            carrier_phase = np.degrees(np.arctan2(carrier_y, carrier_x))
            sideband_r = np.hypot(sideband_x, sideband_y)
            sideband_phase = np.degrees(np.arctan2(sideband_y, sideband_x))
            t_rel = float(times[idx])
            utc = drop_time_utc + t_rel

            data = {
                "iteration": int(iteration),
                "t_rel": t_rel,
                "utc": float(utc),
                "carrier_drive_initial": float(carrier_drive_initial),
                "carrier_drive_final": float(carrier_drive_final),
                "sideband_drive_before_drop": float(sideband_drive_before_drop),
                "sideband_freq_before_drop": float(sideband_freq_before_drop),
                "carrier_timeconstant": float(self.active_carrier_tc),
                "sideband_timeconstant": float(self.active_sideband_tc),
                "carrier_demod_freq": float(carrier_demod_freq),
                "sideband_demod_freq": float(sideband_demod_freq),
                "carrier_X": float(carrier_x),
                "carrier_Y": float(carrier_y),
                "carrier_R": float(carrier_r),
                "carrier_phase": float(carrier_phase),
                "sideband_X": float(sideband_x),
                "sideband_Y": float(sideband_y),
                "sideband_R": float(sideband_r),
                "sideband_phase": float(sideband_phase),
                "carrier_Q_infer": float(carrier_q),
                "carrier_f0_infer": float(carrier_f0),
                "carrier_tau_infer": float(carrier_tau),
                "sideband_Q_infer": float(sideband_q),
                "sideband_f0_infer": float(sideband_f0),
                "sideband_tau_infer": float(sideband_tau),
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
        drive_before_drop: float,
        k_constant: float,
        v0: float,
    ) -> Tuple[float, float, float]:
        if drive_before_drop <= 0 or x == 0 or not np.isfinite(demod_freq):
            return np.nan, np.nan, np.nan
        k_effective = float(k_constant) * float(v0) / float(drive_before_drop)
        q_infer = calculate_Q_infer(x, y, k_effective)
        if not np.isfinite(q_infer) or q_infer == 0:
            return float(q_infer), np.nan, np.nan
        f0_infer = calculate_f0_infer(x, y, demod_freq, k_effective)
        if not np.isfinite(f0_infer) or f0_infer == 0:
            return float(q_infer), float(f0_infer), np.nan
        tau_infer = q_infer / (np.pi * f0_infer)
        return float(q_infer), float(f0_infer), float(tau_infer)

    def _sleep_with_abort(self, duration: float):
        end_time = time.time() + duration
        while time.time() < end_time:
            if self.should_stop():
                break
            time.sleep(min(0.1, end_time - time.time()))


class FreeDecaySidebandWindow(ManagedDockWindow):
    def __init__(self):
        carrier_plot = PlotWidget(
            name="Carrier X vs time",
            columns=FreeDecaySidebandProcedure.DATA_COLUMNS,
            x_axis="t_rel",
            y_axis="carrier_X",
        )
        sideband_plot = PlotWidget(
            name="Sideband R vs time",
            columns=FreeDecaySidebandProcedure.DATA_COLUMNS,
            x_axis="t_rel",
            y_axis="sideband_R",
        )
        phase_plot = PlotWidget(
            name="Phases vs time",
            columns=FreeDecaySidebandProcedure.DATA_COLUMNS,
            x_axis="t_rel",
            y_axis="carrier_phase",
        )

        super().__init__(
            procedure_class=FreeDecaySidebandProcedure,
            inputs=FreeDecaySidebandProcedure.PARAMETERS,
            displays=FreeDecaySidebandProcedure.PARAMETERS,
            x_axis=["t_rel"],
            y_axis=["carrier_X", "carrier_R", "carrier_phase", "sideband_R"],
            widget_list=(carrier_plot, sideband_plot, phase_plot),
            inputs_in_scrollarea=True,
        )

        self.setWindowTitle("Zurich Sideband Free Decay")
        self.filename_prefix = "free-decay-sideband"
        self.directory = self._default_data_dir()

    def queue(self):
        filename = unique_filename(str(self.directory), prefix=f"{self.filename_prefix}_")
        procedure = self.make_procedure()
        results = Results(procedure, filename)
        experiment = self.new_experiment(results)
        self.manager.queue(experiment)

    @staticmethod
    def _default_data_dir() -> Path:
        preferred = Path(r"D:/Data/Fall25-Summer26/free-decay-sideband")
        if preferred.exists():
            return preferred
        fallback = Path(__file__).resolve().parent.parent / "data-files/free-decay-sideband"
        return fallback if fallback.exists() else Path.cwd()


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    window = FreeDecaySidebandWindow()
    window.show()
    sys.exit(app.exec_())
