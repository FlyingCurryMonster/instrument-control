import numpy as np
from pymeasure.display.Qt import QtWidgets
import sys
from pymeasure.instruments.keysight import KeysightE3631A
from pymeasure.experiment import Procedure, Results, unique_filename
from pymeasure.experiment import IntegerParameter, FloatParameter, Parameter
from pymeasure.display.windows.managed_dock_window import ManagedDockWindow
from pymeasure.display.widgets import PlotWidget  # MyPlotWidget
import logging
import time
from pyqtgraph import DateAxisItem


log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

# LabView_t0 = 2082844800
timezone = 4 * 60 * 60

# Temperature <-> Voltage calibration (assume exponential dependence)
# Anchors: at V = 0.0 V -> T = 7 mK, at V = 3.0 V -> T = 100 mK
# Model: T(V) = T0 * exp(k * (V - V0))  => with V0=0 simplifies to T0 * exp(k*V)
T_REF_V0 = 0.0
T_REF_T0 = 7e-3
T_REF_V1 = 3.0
T_REF_T1 = 0.100
# exponential coefficient k
_K_TEMP_V = np.log(T_REF_T1 / T_REF_T0) / (T_REF_V1 - T_REF_V0)

def temp_from_voltage(v):
    """Estimate temperature (K) from applied voltage (V) using exponential model."""
    # use magnitude — sign handled by channel selection
    return T_REF_T0 * np.exp(_K_TEMP_V * (v - T_REF_V0))

def voltage_from_temp(t):
    """Invert temperature to voltage using the exponential model."""
    if t <= 0:
        raise ValueError("Temperature must be positive to invert to voltage")
    return (np.log(t / T_REF_T0) / _K_TEMP_V) + T_REF_V0


class HP_PSU_Ramp(Procedure):
    gpib_address = Parameter('HP E3631A GPIB Address', default='1::2')
    start_voltage = FloatParameter('Start Voltage (V)',)
    stop_voltage = FloatParameter('Stop Voltage (V)', )
    time_duration = FloatParameter('Ramp Duration (hrs)',)
    channel = IntegerParameter('Channel Number',
                               default=1, minimum=1, maximum=3)

    params = [
        'gpib_address',
        'start_voltage',
        'stop_voltage',
        'time_duration',
        'channel',
    ]
    DATA_COLUMNS = [
        'UTC', 'timestamp',
        'control_voltage', 'measured_voltage', 'residual',
    ]

    def startup(self):
        self.psu = KeysightE3631A(f'GPIB{self.gpib_address}::INSTR')
        # Validate duration
        if self.time_duration <= 0:
            raise ValueError('time_duration must be > 0 for log-space ramp')

        # For log-temperature ramp we allow zero voltage (maps to T_REF_T0).
        # Require start and stop have the same sign, unless one is exactly zero.
        if (np.sign(self.start_voltage) != np.sign(self.stop_voltage)) and not (
            self.start_voltage == 0 or self.stop_voltage == 0
        ):
            raise ValueError(
                'Start and stop voltages must have the same sign (or one may be zero) for log-temperature ramp'
            )

        self.control_voltage = self.start_voltage

        if self.channel == 1:
            self.voltage_channel = self.psu.ch_1
            if max(self.start_voltage, self.stop_voltage) > 6:
                raise ValueError("Channel 1 max voltage is 6V")
            if min(self.start_voltage, self.stop_voltage) < 0:
                raise ValueError("Channel 1 min voltage is 0V")
        elif self.channel == 2:
            self.voltage_channel = self.psu.ch_2
            if max(self.start_voltage, self.stop_voltage) > 25:
                raise ValueError("Channel 2 max voltage is 25V")
            if min(self.start_voltage, self.stop_voltage) < 0:
                raise ValueError("Channel 2 min voltage is 0V")
        elif self.channel == 3:
            self.voltage_channel = self.psu.ch_3
            if min(self.start_voltage, self.stop_voltage) < -25:
                raise ValueError("Channel 3 min voltage is -25V")
            if max(self.start_voltage, self.stop_voltage) > 0:
                raise ValueError("Channel 3 max voltage is 0V")

        self.voltage_channel.output_enabled = True
        self.voltage_channel.voltage_setpoint = self.control_voltage
        self.t0 = time.time()

    def execute(self):
        # Implement a ramp that is uniform in log10 space between start and stop
        start = float(self.start_voltage)
        stop = float(self.stop_voltage)

        # If equal, just emit once and return
        if start == stop:
            measured_voltage = self.voltage_channel.voltage
            data = {
                'UTC': time.time(),
                'timestamp': time.time() - self.t0,
                'control_voltage': self.control_voltage,
                'measured_voltage': measured_voltage,
                'residual': measured_voltage - self.control_voltage
            }
            self.emit('results', data)
            return

        # prepare endpoints: we'll interpolate linearly in ln(Temperature)
        # determine sign to set at the end (prefer start's sign, fall back to stop)
        sign = np.sign(start) if start != 0 else (np.sign(stop) if stop != 0 else 1)
        abs_start = abs(start)
        abs_stop = abs(stop)

        # map endpoint voltages to temperatures using calibration model
        T_start = temp_from_voltage(abs_start)
        T_stop = temp_from_voltage(abs_stop)

        ln_T_start = np.log(T_start)
        ln_T_stop = np.log(T_stop)

        total_time = float(self.time_duration) * 3600.0

        # emit initial data point
        measured_voltage = self.voltage_channel.voltage
        data = {
            'UTC': time.time(),
            'timestamp': time.time() - self.t0,
            'control_voltage': self.control_voltage,
            'measured_voltage': measured_voltage,
            'residual': measured_voltage - self.control_voltage
        }
        self.emit('results', data)
        time.sleep(5)

        # Run the ramp, updating every ~5 seconds
        while not self.should_stop():
            elapsed = time.time() - self.t0
            frac = elapsed / total_time
            if frac >= 1.0:
                frac = 1.0

            # interpolate linearly in ln(Temperature) domain, then map back to voltage
            ln_T = ln_T_start + frac * (ln_T_stop - ln_T_start)
            T_target = np.exp(ln_T)
            V_mag = voltage_from_temp(T_target)
            self.control_voltage = sign * V_mag

            self.voltage_channel.voltage_setpoint = self.control_voltage
            measured_voltage = self.voltage_channel.voltage

            data = {
                'UTC': time.time(),
                'timestamp': time.time() - self.t0,
                'control_voltage': self.control_voltage,
                'measured_voltage': measured_voltage,
                'residual': measured_voltage - self.control_voltage
            }
            self.emit('results', data)

            # progress in fraction of total duration (0..100)
            self.emit('progress', np.abs(frac * 100.0))

            # Stop if we've reached the end fraction
            if frac >= 1.0:
                log.info('Reached stop setpoint (log-temperature ramp completed)')
                break

            # additionally, check measured voltage crossing stop depending on ramp direction
            length = stop - start
            if length > 0:
                # ramp increasing: stop is greater than start
                if measured_voltage >= stop:
                    log.warning('Breaking, measured voltage exceeded stop voltage')
                    break
            else:
                # ramp decreasing: stop is less than start
                if measured_voltage <= stop:
                    log.warning('Breaking, measured voltage went below stop voltage')
                    break

            time.sleep(5)


class HP_PSU_Graph(ManagedDockWindow):
    def __init__(self):

        voltage_plot = PlotWidget(
            name='Voltage Plot',
            columns=HP_PSU_Ramp.DATA_COLUMNS,
            x_axis='UTC',
            y_axis='measured_voltage',
        )

        # need to add offset for EST time
        voltage_plot.plot.setAxisItems(
            {'bottom': DateAxisItem(utcOffset=timezone)})

        super().__init__(
            procedure_class=HP_PSU_Ramp,
            inputs=HP_PSU_Ramp.params,
            displays=HP_PSU_Ramp.params,
            x_axis='UTC',
            y_axis='measured_voltage',
            widget_list=(voltage_plot,)
        )

        self.setWindowTitle('HP PSU Ramp Control and Monitoring (log-space)')
        self.directory = r'D:/Data/Fall25-Summer26/HP PSU ramps/'
        self.file_input.filename_fixed = False

    def queue(self):
        directory = self.directory
        filename = unique_filename(directory, prefix=f'{self.filename}_')
        procedure = self.make_procedure()
        results = Results(procedure, filename)
        experiment = self.new_experiment(results)
        self.manager.queue(experiment)


if __name__ == '__main__':
    app = QtWidgets.QApplication([])
    window = HP_PSU_Graph()
    window.show()
    sys.exit(app.exec_())
