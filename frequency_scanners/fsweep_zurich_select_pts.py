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
    linewidth = FloatParameter('linewidth', units='Hz')
    resonance_pt = FloatParameter('Resonance guess (Hz)')
    n_pts = IntegerParameter('Number of points', default=21)
    delay = FloatParameter('Delay (s)', default=1)

    osc_num = IntegerParameter('Oscillator number', default=2)
    demod_num = IntegerParameter('Demodulator number', default=1)
    zur_id = Parameter('Zurich addr.', default='dev4934')
    drive_amp = FloatParameter('Drive Amplitude (V)')

    params = [
        'linewidth', 'resonance_pt', 'n_pts',
        'delay',
        'zur_id', 'osc_num', 'demod_num', 'drive_amp',
    ]

    DATA_COLUMNS = ['UTC', 'timestamp', 'f', 'R', 'phase', 'X', 'Y', 'drive_amp']

    def startup(self):
        log.info('Starting Zurich freq sweeper, with zurich drive and demod')

        self.osc_num -= 1
        self.demod_num -= 1

        self.daq = zhinst.core.ziDAQServer('localhost', 8004, 6)
        self.daq.connectDevice(self.zur_id, interface='1GbE')
        self.daq.set(f"/{self.zur_id}/demods/{self.demod_num}/enable", 1)
        
        # Revised construction: ensure at least half the points lie inside
        # the linewidth, split the remaining points (outside) between left
        # and right. This handles odd n_pts correctly.
        n_total = int(self.n_pts)
        n_inner = (n_total + 1) // 2  # ceil(n_total/2)
        n_outside = n_total - n_inner
        n_left = n_outside // 2
        n_right = n_outside - n_left

        left_pts = np.linspace(
            self.resonance_pt - 3*self.linewidth,
            self.resonance_pt - self.linewidth,
            n_left,
            endpoint=False)

        inner_pts = np.linspace(
            self.resonance_pt - self.linewidth,
            self.resonance_pt + self.linewidth,
            n_inner,
            endpoint=False)

        right_pts = np.linspace(
            self.resonance_pt + self.linewidth,
            self.resonance_pt + 3*self.linewidth,
            n_right,
            endpoint=True)

        # final frequency list for the sweep
        self.freq = np.concatenate([left_pts, inner_pts, right_pts])

        self.t_start = time.time()
        self.zurich_set_amp(self.osc_num, self.drive_amp)
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
                'R': np.sqrt(xzur**2 + yzur**2),
                'phase': np.rad2deg(np.arctan(yzur, xzur)),
                'X': xzur,
                'Y': yzur,
                'drive_amp': self.zurich_get_amp(self.osc_num),
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

    def zurich_get_amp(self, osc_num):
        osc_path = f'/{self.zur_id}/sigouts/0/amplitudes/{osc_num}'
        return self.daq.getDouble(osc_path)

    def zurich_set_amp(self, osc_num, amp):
        osc_path = f'/{self.zur_id}/sigouts/0/amplitudes/{osc_num}'
        self.daq.setDouble(osc_path, amp)

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
