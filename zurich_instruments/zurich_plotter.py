import logging
import sys
import time
import numpy as np
from pymeasure.display.Qt import QtWidgets
from pymeasure.display.windows import ManagedWindow
from pymeasure.experiment import Procedure, Results, unique_filename
from pymeasure.experiment import IntegerParameter, FloatParameter, Parameter
import zhinst.core


log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


class zurich_measure(Procedure):
    sample_rate = FloatParameter('Sample rate', units='Hz', default=1)
    osc_num = IntegerParameter('Oscillator number', default=2)
    demod_num = IntegerParameter('Demodulator number', default=1)
    zur_id = Parameter('Zurich addr.', default='dev4934')
    comments = Parameter('Comments/Notes')

    params = [
        'sample_rate',
        'zur_id', 'osc_num', 'demod_num',
        'comments',
    ]

    # Updated data columns for continuous measurement
    DATA_COLUMNS = [
        'UTC', 'timestamp',
        'X', 'Y', 'R', 'phase',
        'V_drive', 'f_drive',
        ]

    def startup(self):
        log.info('Starting Zurich continuous measurement')
        self.osc_num -= 1
        self.demod_num -= 1

        zurich_ip_address = "192.168.77.26"
        port = 8004
        interface = 'PCIe'
        self.daq = zhinst.core.ziDAQServer(zurich_ip_address, port, 6)
        self.daq.connectDevice(self.zur_id, interface=interface)
        self.daq.set(f"/{self.zur_id}/demods/{self.demod_num}/enable", 1)

        self.t_start = time.time()
        time.sleep(1/self.sample_rate)  # Wait for 1/sample rate

    def execute(self):
        while not self.should_stop():
            utc_time = time.time()
            ts = utc_time - self.t_start
            xzur, yzur, freq_meas = self.zurich_sample_read()
            drive = self.zurich_get_amp(osc_num=self.osc_num)

            data = {
                'UTC': utc_time,
                'timestamp': ts,
                'X': xzur,
                'Y': yzur,
                'R': np.sqrt(xzur**2 + yzur**2),
                'phase': np.rad2deg(np.arctan(yzur/xzur)), 
                'V_drive': drive,
                'f_drive': freq_meas,
            }
            self.emit('results', data)
            time.sleep(1/self.sample_rate)

    def zurich_sample_read(self):
        demod_path = f"/{self.zur_id}/demods/{self.demod_num}/sample"
        resp = self.daq.getSample(demod_path)
        # x, y, f = resp['x'][0], resp['y'][0], resp['frequency'][0]
        x = resp['x'][0]
        y = resp['y'][0]
        f = resp['frequency'][0]
        return x, y, f

    def zurich_get_amp(self, osc_num):
        osc_path = f'/{self.zur_id}/sigouts/0/amplitudes/{osc_num}'
        return self.daq.getDouble(osc_path)

    def zurich_set_freq(self, osc_num, f):
        osc_path = f'{self.zur_id}/oscs/{osc_num}/freq'
        self.daq.setDouble(osc_path, f)


class zurich_graph(ManagedWindow):
    def __init__(self):
        super().__init__(
            procedure_class=zurich_measure,
            inputs=zurich_measure.params,
            displays=zurich_measure.params,
            x_axis='timestamp',
            y_axis='Y',
        )
        self.setWindowTitle('Zurich continuous plotter')
        self.directory = r'D:/Data/RNB-Spring2025/Python Zurich Plotter Data'
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
    window = zurich_graph()
    window.show()
    sys.exit(app.exec_())
