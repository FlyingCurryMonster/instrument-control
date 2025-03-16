import logging
import sys
import time
from pymeasure.display.Qt import QtWidgets
from pymeasure.display.windows import ManagedWindow
from pymeasure.experiment import Procedure, Results, unique_filename
from pymeasure.experiment import IntegerParameter, FloatParameter, Parameter
import zhinst.core


log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


def calculate_Q_infer(X, Y, k):
    return k * (X**2 + Y**2) / X


def calculate_f0_infer(X, Y, f_drive, k):
    Q = calculate_Q_infer(X, Y, k)
    return f_drive * (1 + Y / (X * 2 * Q))


class zurich_measure(Procedure):
    k = FloatParameter('k constant', units='1/V', default=211.85e6)
    sample_rate = FloatParameter('Sample rate', units='Hz', default=1)
    osc_num = IntegerParameter('Oscillator number', default=2)
    demod_num = IntegerParameter('Demodulator number', default=1)
    zur_id = Parameter('Zurich addr.', default='dev4934')
    comments = Parameter('Comments/Notes')

    params = [
        'k',
        'sample_rate',
        'zur_id', 'osc_num', 'demod_num',
        'comments',
    ]

    # Updated data columns for continuous measurement
    DATA_COLUMNS = [
        'UTC', 'timestamp',
        'Q_infer', 'f0_infer',
        'X', 'Y',
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
            xzur, yzur, drive_freq = self.zurich_sample_read()
            drive = self.zurich_get_amp(osc_num=self.osc_num)

            Q_infer = calculate_Q_infer(xzur, yzur, self.k)
            f0_infer = calculate_f0_infer(xzur, yzur, drive_freq, self.k)

            data = {
                'UTC': utc_time,
                'timestamp': ts,
                'Q_infer': Q_infer,
                'f0_infer': f0_infer,
                'X': xzur,
                'Y': yzur,
                'V_drive': drive,
                'f_drive': drive_freq,
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
            y_axis='f0_infer',
        )
        self.setWindowTitle('Zurich continuous plotter')
        self.directory = r'D:/Data/RNB-Spring2025/Zurich fixed freq PLL data'
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
