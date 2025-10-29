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
        'control_voltage', 'measured_voltage',
    ]

    def startup(self):
        self.psu = KeysightE3631A(f'GPIB{self.gpib_address}::INSTR')
        self.control_voltage = self.start_voltage

        if self.channel == 1:
            voltage_channel = self.psu.ch_1
            if max(self.start_voltage, self.stop_voltage) > 6:
                raise ValueError("Channel 1 max voltage is 6V")
            if min(self.start_voltage, self.stop_voltage) < 0:
                raise ValueError("Channel 1 min voltage is 0V")
        elif self.channel == 2:
            voltage_channel = self.psu.ch_2
            if max(self.start_voltage, self.stop_voltage) > 25:
                raise ValueError("Channel 2 max voltage is 25V")
            if min(self.start_voltage, self.stop_voltage) < 0:
                raise ValueError("Channel 2 min voltage is 0V")
        elif self.channel == 3:
            voltage_channel = self.psu.ch_3
            if min(self.start_voltage, self.stop_voltage) < -25:
                raise ValueError("Channel 3 min voltage is -25V")
            if max(self.start_voltage, self.stop_voltage) > 0:
                raise ValueError("Channel 3 max voltage is 0V")

        voltage_channel.voltage_setpoint = self.control_voltage
        voltage_channel.output_enabled = True
        self.t0 = time.time()

    def execute(self):
        length = self.stop_voltage - self.start_voltage
        rate = length / (self.time_duration * 3600)  # V/s

        end_time = time.time() + self.time_duration * 3600
        while (
            # self.control_voltage <= self.stop_voltage
            np.sign(length) * (self.stop_voltage - self.control_voltage) > 0
            and time.time() < end_time
        ):

            self.control_voltage += rate * 5  # update every 5 seconds
            if self.control_voltage > self.stop_voltage:
                self.control_voltage = self.stop_voltage
            self.psu.ch_1.voltage_setpoint = self.control_voltage
            measured_voltage = self.psu.ch_1.voltage_measured

            data = {
                'UTC': time.time(),
                'timestamp': time.time() - self.t0,
                'control_voltage': self.control_voltage,
                'measured_voltage': measured_voltage,
            }

            self.emit('results', data)

            if length > 0:
                if self.control_voltage >= self.stop_voltage:
                    log.warning(
                        'Breaking, control voltage exceeded stop voltage'
                    )
                    break
            if length < 0:
                if self.control_voltage <= self.stop_voltage:
                    log.warning(
                        'Breaking, control voltage went below stop voltage'
                    )
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

        self.setWindowTitle('HP PSU Ramp Control and Monitoring')
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
