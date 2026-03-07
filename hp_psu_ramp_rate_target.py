import logging
import sys
import time
from collections import deque

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
from pymeasure.instruments.keysight import KeysightE3631A
from pyqtgraph import DateAxisItem


log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

timezone = 4 * 60 * 60
RAMP_RATE_LIMIT_V_PER_HR = 0.1  # 100 mV/hr


class HP_PSU_RateTargetRamp(Procedure):
    gpib_address = Parameter("HP E3631A GPIB Address", default="1::2")
    target_voltage = FloatParameter("Target Voltage (V)")
    ramp_rate = FloatParameter("Ramp Rate (V/hr)")
    ramp_rate_limit_override = BooleanParameter(
        "Ramp Rate Limit Override", default=False
    )
    offset_seed = FloatParameter("Offset Seed (V)", default=0.0)
    offset_window = IntegerParameter(
        "Offset Rolling Window", default=100, minimum=1
    )
    update_period_s = FloatParameter("Update Period (s)", default=5.0)
    channel = IntegerParameter("Channel Number", default=1, minimum=1, maximum=3)

    params = [
        "gpib_address",
        "target_voltage",
        "ramp_rate",
        "ramp_rate_limit_override",
        "offset_seed",
        "offset_window",
        "update_period_s",
        "channel",
    ]

    DATA_COLUMNS = [
        "UTC",
        "timestamp",
        "dt",
        "target_voltage",
        "control_voltage",
        "measured_voltage",
        "residual",
        "offset_sample",
        "offset_avg",
    ]

    def startup(self):
        self.psu = KeysightE3631A(f"GPIB{self.gpib_address}::INSTR")

        if self.ramp_rate <= 0:
            raise ValueError("ramp_rate must be > 0 (V/hr)")
        if self.update_period_s <= 0:
            raise ValueError("update_period_s must be > 0")
        if self.offset_window < 1:
            raise ValueError("offset_window must be >= 1")

        if self.channel == 1:
            self.voltage_channel = self.psu.ch_1
            self.v_min, self.v_max = 0.0, 6.0
        elif self.channel == 2:
            self.voltage_channel = self.psu.ch_2
            self.v_min, self.v_max = 0.0, 25.0
        elif self.channel == 3:
            self.voltage_channel = self.psu.ch_3
            self.v_min, self.v_max = -25.0, 0.0
        else:
            raise ValueError("Unsupported channel")

        if self.target_voltage < self.v_min or self.target_voltage > self.v_max:
            raise ValueError(
                f"Target voltage {self.target_voltage}V is outside channel limits "
                f"[{self.v_min}, {self.v_max}]V"
            )

        self.voltage_channel.output_enabled = True

        measured_now = float(self.voltage_channel.voltage)
        self.control_voltage = measured_now
        self.initial_measured_voltage = measured_now
        self.ramp_direction = np.sign(self.target_voltage - measured_now)
        requested_rate_v_per_hr = float(self.ramp_rate)
        if not bool(self.ramp_rate_limit_override):
            if requested_rate_v_per_hr > RAMP_RATE_LIMIT_V_PER_HR:
                log.warning(
                    "Requested ramp_rate %.6f V/hr exceeds limit %.6f V/hr; clamping",
                    requested_rate_v_per_hr,
                    RAMP_RATE_LIMIT_V_PER_HR,
                )
            self.effective_ramp_rate_v_per_hr = min(
                requested_rate_v_per_hr, RAMP_RATE_LIMIT_V_PER_HR
            )
        else:
            self.effective_ramp_rate_v_per_hr = requested_rate_v_per_hr
            if requested_rate_v_per_hr > RAMP_RATE_LIMIT_V_PER_HR:
                log.warning(
                    "Ramp rate limit override enabled: using %.6f V/hr (> %.6f V/hr)",
                    requested_rate_v_per_hr,
                    RAMP_RATE_LIMIT_V_PER_HR,
                )

        self.ramp_rate_v_per_s = self.effective_ramp_rate_v_per_hr / 3600.0

        self.offset_history = deque(maxlen=int(self.offset_window))
        self.offset_history.append(float(self.offset_seed))
        self.offset_avg = float(self.offset_seed)

        self.t0 = time.time()
        self.last_update = time.monotonic()

    def _emit_point(self, dt, measured_voltage, offset_sample, offset_avg):
        data = {
            "UTC": time.time(),
            "timestamp": time.time() - self.t0,
            "dt": dt,
            "target_voltage": float(self.target_voltage),
            "control_voltage": float(self.control_voltage),
            "measured_voltage": float(measured_voltage),
            "residual": float(measured_voltage) - float(self.control_voltage),
            "offset_sample": float(offset_sample),
            "offset_avg": float(offset_avg),
        }
        self.emit("results", data)

    def execute(self):
        measured_now = float(self.voltage_channel.voltage)
        initial_offset_sample = self.control_voltage - measured_now
        self.offset_history.append(initial_offset_sample)
        self.offset_avg = float(np.mean(self.offset_history))
        self._emit_point(
            dt=0.0,
            measured_voltage=measured_now,
            offset_sample=initial_offset_sample,
            offset_avg=self.offset_avg,
        )

        if self.ramp_direction == 0:
            self.emit("progress", 100.0)
            log.info("Measured voltage already at target; no ramp needed")
            return

        total_span = abs(float(self.target_voltage) - self.initial_measured_voltage)
        if total_span == 0:
            total_span = 1.0

        while not self.should_stop():
            time.sleep(float(self.update_period_s))
            now = time.monotonic()
            dt = now - self.last_update
            self.last_update = now

            measured_now = float(self.voltage_channel.voltage)
            offset_sample = self.control_voltage - measured_now
            self.offset_history.append(offset_sample)
            self.offset_avg = float(np.mean(self.offset_history))

            error_to_target = float(self.target_voltage) - measured_now
            if self.ramp_direction > 0 and error_to_target <= 0:
                self._emit_point(
                    dt=dt,
                    measured_voltage=measured_now,
                    offset_sample=offset_sample,
                    offset_avg=self.offset_avg,
                )
                self.emit("progress", 100.0)
                log.info("Reached target voltage (increasing ramp)")
                break
            if self.ramp_direction < 0 and error_to_target >= 0:
                self._emit_point(
                    dt=dt,
                    measured_voltage=measured_now,
                    offset_sample=offset_sample,
                    offset_avg=self.offset_avg,
                )
                self.emit("progress", 100.0)
                log.info("Reached target voltage (decreasing ramp)")
                break

            desired_next_measured = (
                measured_now + self.ramp_direction * self.ramp_rate_v_per_s * dt
            )
            if self.ramp_direction > 0:
                desired_next_measured = min(
                    desired_next_measured, float(self.target_voltage)
                )
            else:
                desired_next_measured = max(
                    desired_next_measured, float(self.target_voltage)
                )

            new_setpoint = desired_next_measured + self.offset_avg
            new_setpoint = max(self.v_min, min(self.v_max, new_setpoint))

            self.voltage_channel.voltage_setpoint = new_setpoint
            self.control_voltage = float(new_setpoint)

            measured_after = float(self.voltage_channel.voltage)
            self._emit_point(
                dt=dt,
                measured_voltage=measured_after,
                offset_sample=offset_sample,
                offset_avg=self.offset_avg,
            )

            progress = abs(measured_now - self.initial_measured_voltage) / total_span * 100.0
            self.emit("progress", min(100.0, max(0.0, progress)))

        if self.should_stop():
            log.info("Stopping ramp as requested")


class HP_PSU_RateTargetGraph(ManagedDockWindow):
    def __init__(self):
        voltage_plot = PlotWidget(
            name="Voltage Plot",
            columns=HP_PSU_RateTargetRamp.DATA_COLUMNS,
            x_axis="UTC",
            y_axis="measured_voltage",
        )
        voltage_plot.plot.setAxisItems({"bottom": DateAxisItem(utcOffset=timezone)})

        super().__init__(
            procedure_class=HP_PSU_RateTargetRamp,
            inputs=HP_PSU_RateTargetRamp.params,
            displays=HP_PSU_RateTargetRamp.params,
            x_axis="UTC",
            y_axis="measured_voltage",
            widget_list=(voltage_plot,),
        )

        self.setWindowTitle("HP PSU Ramp Control and Monitoring (rate + target)")
        self.directory = r"D:/Data/Fall25-Summer26/HP PSU ramps/"
        self.file_input.filename_fixed = False

    def queue(self):
        directory = self.directory
        filename = unique_filename(directory, prefix=f"{self.filename}_")
        procedure = self.make_procedure()
        results = Results(procedure, filename)
        experiment = self.new_experiment(results)
        self.manager.queue(experiment)


if __name__ == "__main__":
    app = QtWidgets.QApplication([])
    window = HP_PSU_RateTargetGraph()
    window.show()
    sys.exit(app.exec_())
