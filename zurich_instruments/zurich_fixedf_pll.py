import logging
import sys
import time
import numpy as np
from pymeasure.display.Qt import QtWidgets
# from pymeasure.display.windows import ManagedWindow
from pymeasure.display.windows.managed_dock_window import ManagedDockWindow
from pymeasure.display.widgets import PlotWidget  # MyPlotWidget
from pymeasure.experiment import Procedure, Results, unique_filename
from pymeasure.experiment import IntegerParameter, FloatParameter, Parameter
from pyqtgraph import DateAxisItem
import zhinst.core


log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

LabView_t0 = 2082844800
timezone = 4 * 60 * 60


def calculate_Q_infer(X, Y, k):
    return k * (X**2 + Y**2) / X


def calculate_f0_infer(X, Y, f_drive, k):
    Q = calculate_Q_infer(X, Y, k)
    return f_drive * (1 + Y / (X * 2 * Q))


def fdrive_calculator(phi_degrees, Q, f0):
    '''
    for a f0 and Q, compute the fdrive needed to produce a response
    with phase phi.

    Use the result:

    Y/X = Q* (f0^2 - fd^2)/(f_d*f0)

    to derive the expression
    '''
    phi = np.deg2rad(phi_degrees)
    adjust_factor = (phi / Q + np.sqrt(phi**2 / Q**2 + 4)) / 2
    return f0 / adjust_factor


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

    def clear_buffer(self):
        """Clear the buffer by resetting the index and full flag."""
        self.index = 0
        self.full = False
        # Optionally, reinitialize the buffer if needed:
        self.buffer = np.zeros(self.size, dtype=float)

    def zero_the_buffer(self):
        """Set all elements in the buffer to zero
        and reset the index and full flag."""
        self.buffer[:] = 0  # Set every element of the preallocated array to 0
        self.index = 0      # Reset the index back to 0
        self.full = False   # Mark the buffer as not full


class zurich_measure(Procedure):
    k = FloatParameter('k constant', units='1/V', default=9.61315e6)
    xbkg = FloatParameter('X background', units='V', default=10*2*1.633e-6)
    ybkg = FloatParameter('Y background', units='V', default=10*2*-4.21e-6)

    phase_limit = FloatParameter(
        'Phase limit',
        units='deg', default=2)

    ringdown_time = FloatParameter(
        'Mininmum time to wait to rebalance',
        units='s', default=60)

    num_tau = FloatParameter(
        "Number of resonator tau's to wait before rebalance",
        default=5)

    sample_rate = FloatParameter('Sample rate', units='Hz', default=0.33)
    buffer_size = IntegerParameter('Re-balance buffer count', default=300)

    zur_id = Parameter('Zurich addr.', default='dev4934')
    osc_num = IntegerParameter('Oscillator number', default=2)
    demod_num = IntegerParameter('Demodulator number', default=1)
    comments = Parameter('Comments/Notes')

    params = [
        'k', 'phase_limit',
        'xbkg', 'ybkg',
        'num_tau', 'ringdown_time',
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
        self.phase_buffer = FixedSizeBuffer(size=self.buffer_size)
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
            utc_time = time.time() + LabView_t0
            ts = utc_time - self.t_start
            xzur, yzur, drive_freq = self.zurich_sample_read()
            X, Y = xzur - self.xbkg, yzur - self.ybkg
            drive = self.zurich_get_amp(osc_num=self.osc_num)

            Q_infer = calculate_Q_infer(X, Y, self.k)
            f0_infer = calculate_f0_infer(X, Y, drive_freq, self.k)

            tau = self.tau_buffer.get_buffer()[-1]
            last_rebalance_time = self.adjust_times.get_buffer()[-1]
            ringdown = max(self.num_tau * tau, self.ringdown_time)
            delay_sufficient = utc_time - last_rebalance_time > ringdown

            if delay_sufficient:
                phase = np.rad2deg(np.arctan(Y/X))

                self.phase_buffer.append(phase)
                phase_tape = self.phase_buffer.get_buffer()
                phase_out_of_range = np.median(
                    np.abs(phase_tape) > self.phase_limit)

                drive_reset_switch = phase_out_of_range and delay_sufficient
            else:
                drive_reset_switch = False

            data = {
                'UTC': utc_time,
                'timestamp': ts,
                'Q_infer': Q_infer,
                'f0_infer': f0_infer,
                'X': X,
                'Y': Y,
                'V_drive': drive,
                'f_drive': drive_freq,
                'k': self.k,
                'drive_reset': int(drive_reset_switch),
                'ringing_down': int(not delay_sufficient),
            }

            self.emit('results', data)

            if drive_reset_switch:
                median_phase_deviation = np.median(phase_tape)
                new_tau = Q_infer / (np.pi * f0_infer)
                if median_phase_deviation > 0:
                    target_phi = -0.5 * self.phase_limit
                    new_freq = fdrive_calculator(
                        target_phi, Q_infer, f0_infer)

                    self.zurich_set_freq(self.osc_num, new_freq)

                    self.adjust_times.append(utc_time)
                    self.tau_buffer.append(new_tau)
                    self.phase_buffer.zero_the_buffer()

                    change = new_freq - drive_freq
                    log.info(f'phase is HIGH, {median_phase_deviation} deg')
                    log.warning(f'DRIVE changed by {change}Hz')
                    log.info(
                        'Ringdown is {:}*{:.7g} s'.format(
                            self.num_tau, new_tau))
                    # log.info('RESETING THE DRIVE FREQ')

                elif median_phase_deviation < 0:
                    # set the frequency to be a little higher
                    target_phi = 0.5 * self.phase_limit
                    new_freq = fdrive_calculator(
                        target_phi, Q_infer, f0_infer)
                    self.zurich_set_freq(self.osc_num, new_freq)

                    self.adjust_times.append(utc_time)
                    self.tau_buffer.append(new_tau)
                    self.phase_buffer.zero_the_buffer()

                    change = new_freq - drive_freq
                    log.info(f'phase is LOW, {median_phase_deviation} deg')
                    log.warning(f'DRIVE adjusted by {change}Hz')
                    log.info('Ringdown is {:} * {:.3g} s'.format(
                        self.num_tau, new_tau))

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


# class zurich_graph(ManagedWindow):
class zurich_graph(ManagedDockWindow):
    def __init__(self):

        time_axis_plot = PlotWidget(
            name='Datetime Plot',
            columns=zurich_measure.DATA_COLUMNS,
            x_axis='UTC',
            y_axis='f0_infer')

        # need to add offset for EST time
        time_axis_plot.plot.setAxisItems(
            {'bottom': DateAxisItem(utcOffset=LabView_t0 + timezone)})

        nyquist_plot = PlotWidget(
            name='Nyquist',
            columns=zurich_measure.DATA_COLUMNS,
            x_axis='X',
            y_axis='Y',
            # npts=zurich_measure.buffer_size,
        )
        nyquist_plot.plot.getViewBox().setAspectLocked(True, ratio=1.0)

        super().__init__(
            procedure_class=zurich_measure,
            inputs=zurich_measure.params,
            displays=zurich_measure.params,
            x_axis=['UTC'],
            y_axis=['X', 'Y', 'f_drive'],
            widget_list=(time_axis_plot, nyquist_plot,)

        )

        for plot_frame in self.dock_widget.plot_frames:
            plot_frame.plot.setAxisItems(
                {'bottom': DateAxisItem(utcOffset=LabView_t0 + timezone)})

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
