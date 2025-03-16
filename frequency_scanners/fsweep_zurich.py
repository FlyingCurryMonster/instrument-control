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


class zurich_fsweep(Procedure):

    f_start = FloatParameter('start frequency (Hz)', default=500)
    f_final = FloatParameter('stop frequency (Hz)', default=1200)
    f_step = FloatParameter('Frequency Step (Hz)', default=1)
    delay = FloatParameter('Delay (s)', default=1)

    osc_num = IntegerParameter('Oscillator number', default=1)
    demod_num = IntegerParameter('Demodulator number', default=1)
    zur_id = Parameter('Zurich addr.', default='dev4934')

    params = [
        'f_start', 'f_final', 'f_step',
        'delay',
        'zur_id', 'osc_num', 'demod_num',
    ]

    DATA_COLUMNS = ['UTC', 'timestamp', 'f', 'X_zur', 'Y_zur', 'X_sr', 'Y_sr']

    def startup(self):
        log.info('Starting Zurich freq sweeper, with zurich drive and demod')

        self.osc_num -= 1
        self.demod_num -= 1

        self.daq = zhinst.core.ziDAQServer('localhost', 8004, 6)
        self.daq.connectDevice(self.zur_id, interface='1GbE')
        self.daq.set(f"/{self.zur_id}/demods/{self.demod_num}/enable", 1)

        self.freq = np.arange(
            self.f_start,
            self.f_final + self.f_step,
            self.f_step)

        self.t_start = time.time()

        self.zurich_set_freq(self.freq[0])
        time.sleep(self.delay*5)

    def execute(self):
        for i, f in enumerate(self.freq):
            self.zurich_set_freq(f)
            time.sleep(self.delay)
            utc_time = time.time()
            ts = utc_time - self.t_start
            xzur, yzur, freq_meas = self.zurich_sample_read()
            log.info('Zurich frequency set to ={}'.format(freq_meas))
            data = {
                'UTC': utc_time,
                'timestamp': ts,
                'f': freq_meas,
                'x': xzur,
                'y': yzur,
            }

            self.emit('results', data)
            self.emit('progress', 100*(i+1)/len(self.freq))
            if self.should_stop():
                log.warning('Caught the stop flag in the procedure')
                break

    def zurich_sample_read(self):
        demod_path = f"/{self.zur_id}/demods/{self.demod_num}/sample"
        resp = self.daq.getSample(demod_path)
        x, y, f = resp['x'][0], resp['y'][0], resp['frequency'][0]
        return x, y, f

    def zurich_set_freq(self, f):
        osc_path = f'{self.zur_id}/oscs/{self.osc_num}/freq'
        self.daq.setDouble(osc_path, f)


class zurich_graph(ManagedWindow):

    def __init__(self):

        super().__init__(
            procedure_class=zurich_fsweep,
            inputs=zurich_fsweep.params,
            displays=zurich_fsweep.params,
            x_axis='f',
            y_axis='X',
        )

        self.setWindowTitle('Zurich frequency sweeper')
        self.directory = r'D:/Data/Zurich'
        self.file_input.filename_fixed = False

    def queue(self):
        directory = self.directory
        filename = unique_filename(directory, prefix='zurich-')
        procedure = self.make_procedure()
        results = Results(procedure, filename)
        experiment = self.new_experiment(results)
        self.manager.queue(experiment)


if __name__ == '__main__':
    app = QtWidgets.QApplication([])
    window = zurich_graph()
    window.show()
    sys.exit(app.exec_())
