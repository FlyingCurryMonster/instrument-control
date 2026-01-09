import logging
import re
import sys
import time
import numpy as np
from pymeasure.display.windows import ManagedWindow
from pymeasure.experiment import Procedure, Results, unique_filename
from pymeasure.experiment import IntegerParameter, FloatParameter, Parameter, BooleanParameter
from pymeasure.display.inputs import ScientificInput
from pymeasure.display.Qt import QtWidgets
import zhinst.core

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

class HighPrecisionScientificInput(ScientificInput):
    def textFromValue(self, value):
        precision = max(1, getattr(self._parameter, "decimals", 15))
        string = f"{value:.{precision}g}".replace("e+", "e")
        string = re.sub(r"e(-?)0*(\d+)", r"e\1\2", string)
        return string


class zurich_fsweep(Procedure):
    Q_guess = FloatParameter('Q guess', units='unitless')
    resonance_pt = FloatParameter(
        'Resonance guess (Hz)',
        decimals=30,
        ui_class=HighPrecisionScientificInput
    )
    n_pts = IntegerParameter('Number of points', default=21)
    delay = FloatParameter('Delay (s)', default=1)
    reverse = BooleanParameter('Reverse sweep', default=False)

    osc_num_input = IntegerParameter('Oscillator number', default=2)
    demod_num_input = IntegerParameter('Demodulator number', default=1)
    zur_id = Parameter('Zurich addr.', default='dev4934')
    drive_amp = FloatParameter('Drive Amplitude (V)')

    comments = Parameter('Comments/Notes')

    params = [
        'Q_guess', 'resonance_pt', 'n_pts',
        'delay',
        'reverse',
        'zur_id', 'osc_num_input', 'demod_num_input', 'drive_amp',
        'comments',
    ]

    DATA_COLUMNS = ['UTC', 'timestamp', 'f', 'R', 'phase', 'X', 'Y', 'drive_amp', 'direction']

    def startup(self):
        log.info('Starting Zurich freq sweeper, with zurich drive and demod')
        log.info(f'resonance guess at {self.resonance_pt}')
        
        self.osc_num = self.osc_num_input - 1
        self.demod_num = self.demod_num_input - 1

        zurich_ip_address = "192.168.77.26"
        port = 8004
        interface = 'PCIe'
        self.daq = zhinst.core.ziDAQServer(zurich_ip_address, port, 6)
        self.daq.connectDevice(self.zur_id, interface=interface)
        self.daq.set(f"/{self.zur_id}/demods/{self.demod_num}/enable", 1)
        self.linewidth= self.resonance_pt / self.Q_guess
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
        if self.reverse:
            self.freq = self.freq[::-1]
        log.info('Frequency points for sweep: {}'.format(self.freq))

        self.t_start = time.time()
        self.zurich_set_amp(self.osc_num, self.drive_amp)
        log.info('Zurich drive amplitude set to ={}'.format(self.drive_amp))
        self.zurich_set_freq(self.freq[0])
        log.info('Zurich frequency set to ={}'.format(self.freq[0]))
        # time.sleep(self.delay)

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
                'phase': np.rad2deg(np.arctan(yzur/xzur)),
                'X': xzur,
                'Y': yzur,
                'drive_amp': self.zurich_get_amp(self.osc_num),
                'direction': 0 if self.reverse else 1,
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
        self.directory = r'D:/Data/Fall25-Summer26/TO freq-sweeps'
        # D:\Data\Fall25-Summer26\TO freq-sweeps
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
