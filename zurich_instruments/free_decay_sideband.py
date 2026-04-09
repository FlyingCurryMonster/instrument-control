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
    """Step sideband 1 drive from initial to final while recording carrier and both sidebands."""

    MAX_SAFE_DRIVE = 10e-3
    CARRIER_DEMOD_INDEX = 0
    SIDEBAND1_SLOT_INDEX = 0
    SIDEBAND2_SLOT_INDEX = 1

    iterations = IntegerParameter("Loop Iterations", default=1)
    delay_before_drop = FloatParameter("Initial delay", units="s", default=0.2)
    measurement_time = FloatParameter("Measurement time", units="s", default=2000)
    ring_up_time = FloatParameter("Ring-up time", units="s", default=1000)
    poll_interval = FloatParameter("Poll interval", units="s", default=1.0)

    use_current_carrier_drive = BooleanParameter(
        "Use current carrier amplitude/frequency", default=True
    )
    carrier_drive_initial = FloatParameter("Carrier drive initial", units="V", default=0.0)
    carrier_frequency_initial = FloatParameter(
        "Carrier frequency initial",
        units="Hz",
        default=800,
        decimals=10,
        ui_class=HighPrecisionScientificInput,
    )

    use_current_sideband1_drive = BooleanParameter(
        "Use current sideband 1 amplitude/frequency", default=True
    )
    sideband1_drive_initial = FloatParameter("Sideband 1 drive initial", units="V", default=3e-3)
    sideband1_drive_final = FloatParameter("Sideband 1 drive final", units="V", default=0.0)
    sideband1_frequency_initial = FloatParameter(
        "Sideband 1 drive frequency",
        units="Hz",
        default=154.2985,
        decimals=10,
        ui_class=HighPrecisionScientificInput,
    )

    use_current_sideband2_drive = BooleanParameter(
        "Use current sideband 2 amplitude/frequency", default=True
    )
    sideband2_drive_initial = FloatParameter("Sideband 2 drive initial", units="V", default=0.0)
    sideband2_frequency_initial = FloatParameter(
        "Sideband 2 drive frequency",
        units="Hz",
        default=520.9206,
        decimals=10,
        ui_class=HighPrecisionScientificInput,
    )

    use_current_time_constants = BooleanParameter(
        "Use current demod time constants", default=True
    )
    carrier_timeconstant = FloatParameter("Carrier time constant", units="s")
    sideband1_timeconstant = FloatParameter("Sideband 1 time constant", units="s")
    sideband2_timeconstant = FloatParameter("Sideband 2 time constant", units="s")

    use_current_sample_rates = BooleanParameter("Use current demod sample rates", default=False)
    demod_sample_rate = FloatParameter("Demod sample rate", units="Hz", default=8)

    settle_after_set = FloatParameter("Wait after setting values", units="s", default=0.2)

    carrier_xbkg = FloatParameter(
        "Carrier X background before rotation", units="V", default=-2.16e-3
    )
    carrier_ybkg = FloatParameter(
        "Carrier Y background before rotation", units="V", default=0.7886e-3
    )

    sideband1_k = FloatParameter("Sideband 1 k constant", units="1/V", default=3931072.9988525705)
    sideband1_V0 = FloatParameter(
        "Sideband 1 drive that k was obtained at", units="V", default=300e-6
    )

    sideband2_k = FloatParameter("Sideband 2 k constant", units="1/V", default=93260373.7208108)
    sideband2_V0 = FloatParameter(
        "Sideband 2 drive that k was obtained at", units="V", default=267.6e-6
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
        "iterations",
        "delay_before_drop",
        "measurement_time",
        "ring_up_time",
        "poll_interval",
        "use_current_carrier_drive",
        "carrier_drive_initial",
        "carrier_frequency_initial",
        "use_current_sideband1_drive",
        "sideband1_drive_initial",
        "sideband1_drive_final",
        "sideband1_frequency_initial",
        "use_current_sideband2_drive",
        "sideband2_drive_initial",
        "sideband2_frequency_initial",
        "use_current_time_constants",
        "carrier_timeconstant",
        "sideband1_timeconstant",
        "sideband2_timeconstant",
        "use_current_sample_rates",
        "demod_sample_rate",
        "settle_after_set",
        "carrier_xbkg",
        "carrier_ybkg",
        "sideband1_k",
        "sideband1_V0",
        "sideband2_k",
        "sideband2_V0",
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
        "iteration",
        "t_rel",
        "utc",
        "carrier_drive_before_drop",
        "carrier_drive_after_drop",
        "carrier_freq_before_drop",
        "sideband1_drive_before_drop",
        "sideband1_drive_after_drop",
        "sideband1_freq_before_drop",
        "sideband2_drive_before_drop",
        "sideband2_drive_after_drop",
        "sideband2_freq_before_drop",
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
        log.info("Connecting to Zurich Instrument for dual-sideband free decay capture")

        self.restore_carrier_amp = None
        self.restore_sideband1_amp = None
        self.restore_sideband2_amp = None
        self.restore_carrier_freq = None
        self.restore_sideband1_freq = None
        self.restore_sideband2_freq = None
        self.restore_carrier_tc = None
        self.restore_sideband1_tc = None
        self.restore_sideband2_tc = None
        self.restore_carrier_rate = None
        self.restore_sideband1_rate = None
        self.restore_sideband2_rate = None

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
        self.daq.set(f"/{self.zur_id}/demods/{self.CARRIER_DEMOD_INDEX}/enable", 1)
        self.daq.set(f"/{self.zur_id}/demods/{self.sideband1_demod_index}/enable", 1)
        self.daq.set(f"/{self.zur_id}/demods/{self.sideband2_demod_index}/enable", 1)

        self.clockbase = self.daq.getInt(f"/{self.zur_id}/clockbase")

        self.restore_carrier_amp = self._get_carrier_amp()
        self.restore_sideband1_amp = self._get_sideband_amp(self.SIDEBAND1_SLOT_INDEX)
        self.restore_sideband2_amp = self._get_sideband_amp(self.SIDEBAND2_SLOT_INDEX)
        self.restore_carrier_freq = self._get_freq(self.carrier_osc_index)
        self.restore_sideband1_freq = self._get_freq(self.sideband1_osc_index)
        self.restore_sideband2_freq = self._get_freq(self.sideband2_osc_index)
        self.restore_carrier_tc = self._get_timeconstant(self.CARRIER_DEMOD_INDEX)
        self.restore_sideband1_tc = self._get_timeconstant(self.sideband1_demod_index)
        self.restore_sideband2_tc = self._get_timeconstant(self.sideband2_demod_index)
        self.restore_carrier_rate = self._get_rate(self.CARRIER_DEMOD_INDEX)
        self.restore_sideband1_rate = self._get_rate(self.sideband1_demod_index)
        self.restore_sideband2_rate = self._get_rate(self.sideband2_demod_index)

        self._validate_parameters()
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

        if self.use_current_carrier_drive:
            self.target_carrier_amp = self.restore_carrier_amp
            self.target_carrier_freq = self.restore_carrier_freq
            self.carrier_drive_initial = self.target_carrier_amp
            self.carrier_frequency_initial = self.target_carrier_freq
        else:
            self.target_carrier_amp = float(self.carrier_drive_initial)
            self.target_carrier_freq = float(self.carrier_frequency_initial)

        if self.use_current_sideband1_drive:
            self.target_sideband1_amp = self.restore_sideband1_amp
            self.target_sideband1_freq = self.restore_sideband1_freq
            self.sideband1_drive_initial = self.target_sideband1_amp
            self.sideband1_frequency_initial = self.target_sideband1_freq
        else:
            self.target_sideband1_amp = float(self.sideband1_drive_initial)
            self.target_sideband1_freq = float(self.sideband1_frequency_initial)

        if self.use_current_sideband2_drive:
            self.target_sideband2_amp = self.restore_sideband2_amp
            self.target_sideband2_freq = self.restore_sideband2_freq
            self.sideband2_drive_initial = self.target_sideband2_amp
            self.sideband2_frequency_initial = self.target_sideband2_freq
        else:
            self.target_sideband2_amp = float(self.sideband2_drive_initial)
            self.target_sideband2_freq = float(self.sideband2_frequency_initial)

        if self.use_current_time_constants:
            self.active_carrier_tc = self.restore_carrier_tc
            self.active_sideband1_tc = self.restore_sideband1_tc
            self.active_sideband2_tc = self.restore_sideband2_tc
            self.carrier_timeconstant = self.active_carrier_tc
            self.sideband1_timeconstant = self.active_sideband1_tc
            self.sideband2_timeconstant = self.active_sideband2_tc
        else:
            self.active_carrier_tc = float(self.carrier_timeconstant)
            self.active_sideband1_tc = float(self.sideband1_timeconstant)
            self.active_sideband2_tc = float(self.sideband2_timeconstant)
            self._set_timeconstant(self.CARRIER_DEMOD_INDEX, self.active_carrier_tc)
            self._set_timeconstant(self.sideband1_demod_index, self.active_sideband1_tc)
            self._set_timeconstant(self.sideband2_demod_index, self.active_sideband2_tc)

        if self.use_current_sample_rates:
            self.active_carrier_rate = self.restore_carrier_rate
            self.active_sideband1_rate = self.restore_sideband1_rate
            self.active_sideband2_rate = self.restore_sideband2_rate
        else:
            self.active_carrier_rate = float(self.demod_sample_rate)
            self.active_sideband1_rate = float(self.demod_sample_rate)
            self.active_sideband2_rate = float(self.demod_sample_rate)
            self._set_rate(self.CARRIER_DEMOD_INDEX, self.active_carrier_rate)
            self._set_rate(self.sideband1_demod_index, self.active_sideband1_rate)
            self._set_rate(self.sideband2_demod_index, self.active_sideband2_rate)

        self._apply_initial_state()

        changed_anything = (
            (not self.use_current_carrier_drive)
            or (not self.use_current_sideband1_drive)
            or (not self.use_current_sideband2_drive)
            or (not self.use_current_time_constants)
            or (not self.use_current_sample_rates)
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

            pre_drop_carrier_amp = self._get_carrier_amp()
            pre_drop_carrier_freq = self._get_freq(self.carrier_osc_index)
            pre_drop_sideband1_amp = self._get_sideband_amp(self.SIDEBAND1_SLOT_INDEX)
            pre_drop_sideband1_freq = self._get_freq(self.sideband1_osc_index)
            pre_drop_sideband2_amp = self._get_sideband_amp(self.SIDEBAND2_SLOT_INDEX)
            pre_drop_sideband2_freq = self._get_freq(self.sideband2_osc_index)

            self._set_sideband1_amp(self.sideband1_drive_final)
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
                carrier_drive_before_drop=pre_drop_carrier_amp,
                carrier_drive_after_drop=pre_drop_carrier_amp,
                carrier_freq_before_drop=pre_drop_carrier_freq,
                sideband1_drive_before_drop=pre_drop_sideband1_amp,
                sideband1_drive_after_drop=self.sideband1_drive_final,
                sideband1_freq_before_drop=pre_drop_sideband1_freq,
                sideband2_drive_before_drop=pre_drop_sideband2_amp,
                sideband2_drive_after_drop=pre_drop_sideband2_amp,
                sideband2_freq_before_drop=pre_drop_sideband2_freq,
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
        log.info("Restoring original dual-sideband free decay settings")
        try:
            self._restore_full_state()
        except Exception:
            log.exception("Unable to restore the original free decay settings.")
        log.info("Finished")

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
        if self.settle_after_set < 0:
            raise ValueError("settle_after_set must be >= 0")
        if not self.use_current_sample_rates:
            if self.demod_sample_rate is None or self.demod_sample_rate <= 0:
                raise ValueError("demod_sample_rate must be > 0 when not using current values.")
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

        for label, amplitude in (
            ("carrier_drive_initial", self.carrier_drive_initial),
            ("sideband1_drive_initial", self.sideband1_drive_initial),
            ("sideband1_drive_final", self.sideband1_drive_final),
            ("sideband2_drive_initial", self.sideband2_drive_initial),
        ):
            if amplitude is not None:
                self._validate_safe_amplitude(label, amplitude)

        if not self.use_current_carrier_drive:
            if self.carrier_frequency_initial is None or self.carrier_frequency_initial <= 0:
                raise ValueError("carrier_frequency_initial must be > 0 when not using current carrier drive.")
        if not self.use_current_sideband1_drive:
            if self.sideband1_frequency_initial is None or self.sideband1_frequency_initial <= 0:
                raise ValueError("sideband1_frequency_initial must be > 0 when not using current sideband 1 drive.")
        if not self.use_current_sideband2_drive:
            if self.sideband2_frequency_initial is None or self.sideband2_frequency_initial <= 0:
                raise ValueError("sideband2_frequency_initial must be > 0 when not using current sideband 2 drive.")
        if not self.use_current_time_constants:
            for label, value in (
                ("carrier_timeconstant", self.carrier_timeconstant),
                ("sideband1_timeconstant", self.sideband1_timeconstant),
                ("sideband2_timeconstant", self.sideband2_timeconstant),
            ):
                if value is None or value <= 0:
                    raise ValueError(f"{label} must be > 0 when not using current values.")

    def _apply_initial_state(self):
        self._set_carrier_drive(self.target_carrier_amp, self.target_carrier_freq)
        self._set_sideband_drive(
            self.SIDEBAND1_SLOT_INDEX,
            self.sideband1_osc_index,
            self.target_sideband1_amp,
            self.target_sideband1_freq,
        )
        self._set_sideband_drive(
            self.SIDEBAND2_SLOT_INDEX,
            self.sideband2_osc_index,
            self.target_sideband2_amp,
            self.target_sideband2_freq,
        )
        self._log_state(
            "Initial applied state",
            self.target_carrier_amp,
            self.target_sideband1_amp,
            self.target_sideband2_amp,
            self.target_carrier_freq,
            self.target_sideband1_freq,
            self.target_sideband2_freq,
        )

    def _restore_between_iterations(self):
        self._apply_initial_state()

    def _restore_full_state(self):
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
        if self.restore_carrier_freq is not None:
            self.daq.setDouble(self._freq_path(self.carrier_osc_index), float(self.restore_carrier_freq))
        if self.restore_sideband1_freq is not None:
            self.daq.setDouble(self._freq_path(self.sideband1_osc_index), float(self.restore_sideband1_freq))
        if self.restore_sideband2_freq is not None:
            self.daq.setDouble(self._freq_path(self.sideband2_osc_index), float(self.restore_sideband2_freq))
        if self.restore_carrier_tc is not None:
            self.daq.setDouble(self._timeconstant_path(self.CARRIER_DEMOD_INDEX), float(self.restore_carrier_tc))
        if self.restore_sideband1_tc is not None:
            self.daq.setDouble(self._timeconstant_path(self.sideband1_demod_index), float(self.restore_sideband1_tc))
        if self.restore_sideband2_tc is not None:
            self.daq.setDouble(self._timeconstant_path(self.sideband2_demod_index), float(self.restore_sideband2_tc))
        if self.restore_carrier_rate is not None:
            self.daq.setDouble(self._rate_path(self.CARRIER_DEMOD_INDEX), float(self.restore_carrier_rate))
        if self.restore_sideband1_rate is not None:
            self.daq.setDouble(self._rate_path(self.sideband1_demod_index), float(self.restore_sideband1_rate))
        if self.restore_sideband2_rate is not None:
            self.daq.setDouble(self._rate_path(self.sideband2_demod_index), float(self.restore_sideband2_rate))
        self.daq.sync()

    def _record_decay(
        self,
        iteration: int,
        drop_time_utc: float,
        drop_device_ts: Optional[float],
        carrier_drive_before_drop: float,
        carrier_drive_after_drop: float,
        carrier_freq_before_drop: float,
        sideband1_drive_before_drop: float,
        sideband1_drive_after_drop: float,
        sideband1_freq_before_drop: float,
        sideband2_drive_before_drop: float,
        sideband2_drive_after_drop: float,
        sideband2_freq_before_drop: float,
    ):
        t_start = time.time()
        t_end = t_start + self.measurement_time
        self.daq.subscribe(self.carrier_sample_path)
        self.daq.subscribe(self.sideband1_sample_path)
        self.daq.subscribe(self.sideband2_sample_path)

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
                    carrier_drive_before_drop=carrier_drive_before_drop,
                    carrier_drive_after_drop=carrier_drive_after_drop,
                    carrier_freq_before_drop=carrier_freq_before_drop,
                    sideband1_drive_before_drop=sideband1_drive_before_drop,
                    sideband1_drive_after_drop=sideband1_drive_after_drop,
                    sideband1_freq_before_drop=sideband1_freq_before_drop,
                    sideband2_drive_before_drop=sideband2_drive_before_drop,
                    sideband2_drive_after_drop=sideband2_drive_after_drop,
                    sideband2_freq_before_drop=sideband2_freq_before_drop,
                )
                iter_frac = min(1.0, (time.time() - t_start) / self.measurement_time)
                overall = 100.0 * (iteration + iter_frac) / max(1, self.iterations)
                self.emit("progress", overall)
        finally:
            self.daq.unsubscribe(self.carrier_sample_path)
            self.daq.unsubscribe(self.sideband1_sample_path)
            self.daq.unsubscribe(self.sideband2_sample_path)

    def _emit_from_poll(
        self,
        poll_data: Dict,
        iteration: int,
        drop_time_utc: float,
        drop_device_ts: Optional[float],
        carrier_drive_before_drop: float,
        carrier_drive_after_drop: float,
        carrier_freq_before_drop: float,
        sideband1_drive_before_drop: float,
        sideband1_drive_after_drop: float,
        sideband1_freq_before_drop: float,
        sideband2_drive_before_drop: float,
        sideband2_drive_after_drop: float,
        sideband2_freq_before_drop: float,
    ):
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
            len(sideband1_xs),
            len(sideband1_ys),
            len(sideband2_xs),
            len(sideband2_ys),
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
                sideband1_demod_freq,
                sideband1_drive_before_drop,
                self.sideband1_k,
                self.sideband1_V0,
            )
            sideband2_q, sideband2_f0, sideband2_tau = self._infer_metrics(
                sideband2_x,
                sideband2_y,
                sideband2_demod_freq,
                sideband2_drive_before_drop,
                self.sideband2_k,
                self.sideband2_V0,
            )

            t_rel = float(times[idx])
            utc = drop_time_utc + t_rel

            data = {
                "iteration": int(iteration),
                "t_rel": t_rel,
                "utc": float(utc),
                "carrier_drive_before_drop": float(carrier_drive_before_drop),
                "carrier_drive_after_drop": float(carrier_drive_after_drop),
                "carrier_freq_before_drop": float(carrier_freq_before_drop),
                "sideband1_drive_before_drop": float(sideband1_drive_before_drop),
                "sideband1_drive_after_drop": float(sideband1_drive_after_drop),
                "sideband1_freq_before_drop": float(sideband1_freq_before_drop),
                "sideband2_drive_before_drop": float(sideband2_drive_before_drop),
                "sideband2_drive_after_drop": float(sideband2_drive_after_drop),
                "sideband2_freq_before_drop": float(sideband2_freq_before_drop),
                "carrier_timeconstant": float(self.active_carrier_tc),
                "sideband1_timeconstant": float(self.active_sideband1_tc),
                "sideband2_timeconstant": float(self.active_sideband2_tc),
                "carrier_sample_rate": float(self.active_carrier_rate),
                "sideband1_sample_rate": float(self.active_sideband1_rate),
                "sideband2_sample_rate": float(self.active_sideband2_rate),
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

    def _get_carrier_amp(self) -> float:
        return self.daq.getDouble(self._carrier_amp_path())

    def _get_sideband_amp(self, slot_index: int) -> float:
        return self.daq.getDouble(self._sideband_amp_path(slot_index))

    def _set_carrier_amp(self, amplitude: float):
        self._validate_safe_amplitude("carrier amplitude", amplitude)
        self.daq.setDouble(self._carrier_amp_path(), float(amplitude))
        self.daq.sync()

    def _set_carrier_drive(self, amplitude: float, frequency: float):
        self._validate_safe_amplitude("carrier amplitude", amplitude)
        self.daq.setDouble(self._carrier_amp_path(), float(amplitude))
        self.daq.setDouble(self._freq_path(self.carrier_osc_index), float(frequency))
        self.daq.sync()

    def _set_sideband1_amp(self, amplitude: float):
        self._validate_safe_amplitude("sideband 1 amplitude", amplitude)
        self.daq.setDouble(self._sideband_amp_path(self.SIDEBAND1_SLOT_INDEX), float(amplitude))
        self.daq.sync()

    def _set_sideband_drive(self, slot_index: int, osc_index: int, amplitude: float, frequency: float):
        self._validate_safe_amplitude(f"sideband {slot_index} amplitude", amplitude)
        self.daq.setDouble(self._sideband_amp_path(slot_index), float(amplitude))
        self.daq.setDouble(self._freq_path(osc_index), float(frequency))
        self.daq.sync()

    def _get_freq(self, osc_num: int) -> float:
        return self.daq.getDouble(self._freq_path(osc_num))

    def _get_timeconstant(self, demod_index: int) -> float:
        return self.daq.getDouble(self._timeconstant_path(demod_index))

    def _set_timeconstant(self, demod_index: int, value: float):
        self.daq.setDouble(self._timeconstant_path(demod_index), float(value))

    def _get_rate(self, demod_index: int) -> float:
        return self.daq.getDouble(self._rate_path(demod_index))

    def _set_rate(self, demod_index: int, value: float):
        self.daq.setDouble(self._rate_path(demod_index), float(value))

    def _validate_restore_state(self):
        self._validate_safe_amplitude("restore_carrier_amp", self.restore_carrier_amp)
        self._validate_safe_amplitude("restore_sideband1_amp", self.restore_sideband1_amp)
        self._validate_safe_amplitude("restore_sideband2_amp", self.restore_sideband2_amp)

    def _validate_safe_amplitude(self, label: str, amplitude: float):
        if not self._is_safe_amplitude(amplitude):
            raise ValueError(
                f"{label}={float(amplitude):.9g} V exceeds the hard safety limit of "
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
    ):
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


class FreeDecaySidebandWindow(ManagedDockWindow):
    def __init__(self):
        carrier_plot = PlotWidget(
            name="Carrier X vs time",
            columns=FreeDecaySidebandProcedure.DATA_COLUMNS,
            x_axis="t_rel",
            y_axis="carrier_X",
        )
        sideband1_plot = PlotWidget(
            name="Sideband 1 R vs time",
            columns=FreeDecaySidebandProcedure.DATA_COLUMNS,
            x_axis="t_rel",
            y_axis="sideband1_R",
        )
        sideband2_plot = PlotWidget(
            name="Sideband 2 R vs time",
            columns=FreeDecaySidebandProcedure.DATA_COLUMNS,
            x_axis="t_rel",
            y_axis="sideband2_R",
        )
        phase_plot = PlotWidget(
            name="Phases vs time",
            columns=FreeDecaySidebandProcedure.DATA_COLUMNS,
            x_axis="t_rel",
            y_axis="sideband1_phase",
        )

        super().__init__(
            procedure_class=FreeDecaySidebandProcedure,
            inputs=FreeDecaySidebandProcedure.PARAMETERS,
            displays=FreeDecaySidebandProcedure.PARAMETERS,
            x_axis=["t_rel"],
            y_axis=[
                "carrier_X",
                "carrier_R",
                "sideband1_R",
                "sideband2_R",
                "sideband1_phase",
                "sideband2_phase",
            ],
            widget_list=(carrier_plot, sideband1_plot, sideband2_plot, phase_plot),
            inputs_in_scrollarea=True,
        )

        self.setWindowTitle("Zurich Dual-Sideband Free Decay")
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
        preferred = Path(r"D:/Data/Fall25-Summer26/free-decay")
        if preferred.exists():
            return preferred
        fallback = Path(__file__).resolve().parent.parent / "data-files/free-decay"
        return fallback if fallback.exists() else Path.cwd()


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    window = FreeDecaySidebandWindow()
    window.show()
    sys.exit(app.exec_())
