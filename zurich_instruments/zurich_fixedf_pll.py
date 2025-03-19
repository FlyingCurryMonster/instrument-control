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


def calculate_Q_infer(X, Y, k):
    return k * (X**2 + Y**2) / X


def calculate_f0_infer(X, Y, f_drive, k):
    Q = calculate_Q_infer(X, Y, k)
    return f_drive * (1 + Y / (X * 2 * Q))


class FixedSizeBuffer:
    def __init__(self, size):
        self.size = size
        self.buffer = np.empty(size, dtype=float)  # Pre-allocate the buffer
        self.index = 0
        self.full = False

    def append(self, element):
        """Add a new element to the buffer."""
        self.buffer[self.index] = element

        # Wrap around when the buffer is full
        self.index = (self.index + 1) % self.size
  
        if self.index == 0:
            self.full = True

    def get_buffer(self):
        """Return the current buffer as a NumPy array."""
        if self.full:
            return self.buffer
        # Only return valid data if buffer isn't full        
        return self.buffer[:self.index]


class zurich_measure(Procedure):
    k = FloatParameter('k constant', units='1/V', default=205.425e6)
    
    f0_infer_limit = FloatParameter(
        'Frequency deviation limit',
        units='Hz', default=1e-3)

    ringdown_time = FloatParameter(
        'Mininmum time to wait to rebalance',
        units='s', default=60)

    sample_rate = FloatParameter('Sample rate', units='Hz', default=1)
    buffer_size = IntegerParameter('Re-balance buffer count', default=30)

    zur_id = Parameter('Zurich addr.', default='dev4934')
    osc_num = IntegerParameter('Oscillator number', default=2)
    demod_num = IntegerParameter('Demodulator number', default=1)
    comments = Parameter('Comments/Notes')

    params = [
        'k', 'f0_infer_limit', 'ringdown_time',
        'sample_rate', 'buffer_size',
        'zur_id', 'osc_num', 'demod_num',
        'comments',
    ]

    # Updated data columns for continuous measurement
    DATA_COLUMNS = [
        'UTC', 'timestamp',
        'Q_infer', 'f0_infer',
        'X', 'Y',
        'V_drive', 'f_drive', 'k',
        'drive_reset', 'ringing_down',
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

        self.adjust_times = FixedSizeBuffer(size=self.buffer_size)
        self.f0_buffer = FixedSizeBuffer(size=self.buffer_size)
        self.tau_buffer = FixedSizeBuffer(size=self.buffer_size)

        self.adjust_times.append(0)

        xzur, yzur, drive_freq = self.zurich_sample_read()
        Q_infer = calculate_Q_infer(xzur, yzur, self.k)
        f0_infer = calculate_f0_infer(xzur, yzur, drive_freq, self.k)
        tau_initial = Q_infer / (np.pi * f0_infer)

        self.tau_buffer.append(tau_initial)
        self.t_start = time.time()

    def execute(self):
        while not self.should_stop():
            utc_time = time.time()
            ts = utc_time - self.t_start
            xzur, yzur, drive_freq = self.zurich_sample_read()
            drive = self.zurich_get_amp(osc_num=self.osc_num)

            Q_infer = calculate_Q_infer(xzur, yzur, self.k)
            f0_infer = calculate_f0_infer(xzur, yzur, drive_freq, self.k)

            f0_deviation = f0_infer - drive_freq
            self.f0_buffer.append(f0_deviation)
            f0_tape = self.f0_buffer.get_buffer()            
            f0_out_of_range = np.median(np.abs(f0_tape) > self.f0_infer_limit)

            tau = self.tau_buffer.get_buffer()[-1]
            last_rebalance_time = self.adjust_times.get_buffer()[-1]

            ringdown = max(3 * tau, self.ringdown_time)
            delay_sufficient = utc_time - last_rebalance_time > ringdown

            drive_reset_switch = f0_out_of_range and delay_sufficient

            data = {
                'UTC': utc_time,
                'timestamp': ts,
                'Q_infer': Q_infer,
                'f0_infer': f0_infer,
                'X': xzur,
                'Y': yzur,
                'V_drive': drive,
                'f_drive': drive_freq,
                'k': self.k,
                'drive_reset': int(drive_reset_switch),
                'ringing_down': int(not delay_sufficient),
            }

            self.emit('results', data)

            if drive_reset_switch:
                median_f0infer_deviation = np.median(f0_tape)
                if median_f0infer_deviation > 0:
                    # set the new frequency to be a little 
                    # not sure ask JMP if i should do that
                    new_freq = f0_infer - 1 * self.f0_infer_limit / 5
                    self.zurich_set_freq(self.osc_num, new_freq)
                    self.adjust_times.append(utc_time)
                    self.tau_buffer.append(tau)
                    log.info(f'f0 infer is {median_f0infer_deviation}'
                             'Hz ABOVE the drive')
                    log.info(f'DRIVE RESET TO {new_freq}')
                    # log.info('RESETING THE DRIVE FREQ')

                elif median_f0infer_deviation < 0:
                    # set the frequency to be a little higher
                    new_freq = f0_infer + 1 * self.f0_infer_limit / 5
                    self.zurich_set_freq(self.osc_num, new_freq)
                    self.adjust_times.append(utc_time)
                    self.tau_buffer.append(tau)
                    log.info(f'f0 infer is {median_f0infer_deviation}Hz'
                             'BELOW the drive')
                    log.info(f'DRIVE RESET TO {new_freq}')

            time.sleep(1/self.sample_rate)

    def zurich_sample_read(self):
        demod_path = f"/{self.zur_id}/demods/{self.demod_num}/sample"
        resp = self.daq.getSample(demod_path)
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
        self.setWindowTitle('Zurich fixed freq PLL')
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
