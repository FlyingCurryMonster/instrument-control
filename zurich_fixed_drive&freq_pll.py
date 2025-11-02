import logging
import sys
import time
import numpy as np
from datetime import datetime, timezone as dt_timezone
from zoneinfo import ZoneInfo
from pymeasure.display.Qt import QtWidgets
# from pymeasure.display.windows import ManagedWindow
from pymeasure.display.windows.managed_dock_window import ManagedDockWindow
from pymeasure.display.widgets import PlotWidget  # MyPlotWidget
from pymeasure.experiment import Procedure, Results, unique_filename
from pymeasure.experiment import IntegerParameter, FloatParameter, Parameter, BooleanParameter
from pyqtgraph import DateAxisItem
import pyqtgraph as pg
import zhinst.core


log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

LabView_t0 = 2082844800
# Use an IANA timezone name so DST is handled correctly. Change this to your
# local timezone if needed (e.g. "America/New_York").
TZ_NAME = "America/New_York"

def get_local_utc_offset_seconds(tz_name=TZ_NAME, timestamp=None):
    """Return local UTC offset in seconds for tz_name at given timestamp.

    If timestamp is None, uses the current time. The result already includes
    DST adjustments when applicable.
    """
    if timestamp is None:
        timestamp = time.time()
    # Convert to an aware UTC datetime, then convert to the requested zone
    dt_utc = datetime.fromtimestamp(timestamp, dt_timezone.utc)
    local_dt = dt_utc.astimezone(ZoneInfo(tz_name))
    offset = local_dt.utcoffset()
    return int(offset.total_seconds()) if offset is not None else 0


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

# Not necessary, you just want to control delta V over V
def vane_motion(
        x, y, detect_seperation,
        C_detect, C_cable,
        V_bias, preamp_gain):
    r = np.sqrt(x ** 2 + y ** 2) / preamp_gain
    C_factor = 1 + C_cable / C_detect

    delta_d = detect_seperation * C_factor * r / V_bias
    return delta_d

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
    k = FloatParameter('k constant', units='1/V', default=5.15615e7)
    xbkg = FloatParameter('X background', units='V', default=0)
    ybkg = FloatParameter('Y background', units='V', default=0)

    amp_pid = BooleanParameter('Amplitude PID', default=True)
    amp_band = FloatParameter('Allowed amplitude deviation', units='V', default=0.05e-3)
    center_amp = FloatParameter('Target amplitude', units='V', default=1e-3)

    phase_limit = FloatParameter(
        'Phase limit',
        units='deg', default=360)

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
        'amp_pid', 'amp_band', 'center_amp',
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
        self.amp_buffer = FixedSizeBuffer(size=self.buffer_size)

        self.adjust_times.append(0)

        xzur, yzur, drive_freq = self.zurich_sample_read()
        Q_infer = calculate_Q_infer(xzur, yzur, self.k)
        f0_infer = calculate_f0_infer(xzur, yzur, drive_freq, self.k)
        tau_initial = Q_infer / (np.pi * f0_infer)

        self.tau_buffer.append(tau_initial)
        # Record timezone name for runtime offset calculations
        self.tz_name = TZ_NAME

        # Record start time in the same LabView + local-offset scale used for UTC
        now_unix = time.time()
        self.t_start = now_unix + LabView_t0 + get_local_utc_offset_seconds(self.tz_name, timestamp=now_unix)

    def execute(self):
        while not self.should_stop():
            now_unix = time.time()
            # Compute UTC time in LabView epoch plus current local offset (handles DST)
            utc_time = now_unix + LabView_t0 + get_local_utc_offset_seconds(self.tz_name, timestamp=now_unix)
            ts = utc_time - self.t_start
            xzur, yzur, drive_freq = self.zurich_sample_read()
            X, Y = xzur - self.xbkg, yzur - self.ybkg
            drive = self.zurich_get_amp(osc_num=self.osc_num)

            Q_infer = calculate_Q_infer(X, Y, self.k)
            f0_infer = calculate_f0_infer(X, Y, drive_freq, self.k)

            tau = self.tau_buffer.get_buffer()[-1]
            last_rebalance_time = self.adjust_times.get_buffer()[-1]
            ringdown = max(self.num_tau * tau, self.ringdown_time)
            delay_sufficient = (utc_time - last_rebalance_time) > ringdown

            if delay_sufficient:
                phase = np.rad2deg(np.arctan(Y/X))

                self.phase_buffer.append(phase)
                phase_tape = self.phase_buffer.get_buffer()
                phase_out_of_range = np.median(
                    np.abs(phase_tape)) > self.phase_limit

                self.amp_buffer.append(np.sqrt(X**2 + Y**2) - self.center_amp)
                amp_tape = self.amp_buffer.get_buffer()
                amp_out_of_range = np.median(
                    np.abs(amp_tape)) > self.amp_band
                

                freq_reset_switch = phase_out_of_range and delay_sufficient
                amp_reset_switch = amp_out_of_range and self.amp_pid and delay_sufficient
            else:
                freq_reset_switch = False
                amp_reset_switch = False

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
                'drive_reset': int(freq_reset_switch or amp_reset_switch),
                'ringing_down': int(not delay_sufficient),
            }

            self.emit('results', data)
               

            if freq_reset_switch or amp_reset_switch:

                freq_reset_procedure()
                if self.amp_pid:
                    amp_reset_procedure()
                    self.amp_buffer.zero_the_buffer()
                
                new_tau = Q_infer / (np.pi * f0_infer)
                self.adjust_times.append(utc_time)
                self.tau_buffer.append(new_tau)
                self.phase_buffer.zero_the_buffer()

            else:
                pass
            
            def amp_reset_procedure():
                median_amp_deviation = np.median(amp_tape)
                
                
                # amp is too high, need to reduce the drive
                if median_amp_deviation > 0:
                    target_amp = -0.5 * self.amp_band + self.center_amp
                    drive_scale_factor = (target_amp) / (data['X'] **2 + data['Y'] **2) ** 0.5

                    new_drive = drive * drive_scale_factor
                    self.zurich_set_amp(self.osc_num, new_drive)

                    log.info(f'amplitude is off, {median_amp_deviation} V')
                    log.warning(f'DRIVE changed by factor of {new_drive/drive}')
                    log.info(
                        'Ringdown is {:}*{:.7g} s'.format(
                            self.num_tau, new_tau))
                    

                    self.k = self.k * (drive / new_drive)
                    log.info(f'k adjusted to {self.k}')
                # amp is too low, need to increase the drive
                
                elif median_amp_deviation < 0:
                    target_amp = 0.5 * self.amp_band + self.center_amp
                    drive_scale_factor = (target_amp) / (data['X'] **2 + data['Y'] **2) ** 0.5

                    new_drive = drive * drive_scale_factor
                    self.zurich_set_amp(self.osc_num, new_drive)

                    log.info(f'amplitude is off, {median_amp_deviation} V')
                    log.warning(f'DRIVE adjusted by factor of {new_drive/drive}')
                    log.info('Ringdown is {:} * {:.3g} s'.format(
                        self.num_tau, new_tau))            

                    self.k = self.k * (drive / new_drive)
                    log.info(f'k adjusted to {self.k}')

            def freq_reset_procedure():
                median_phase_deviation = np.median(phase_tape)
                new_tau = Q_infer / (np.pi * f0_infer)
                if median_phase_deviation > 0:
                    target_phi = -0.5 * self.phase_limit
                    new_freq = fdrive_calculator(
                        target_phi, Q_infer, f0_infer)

                    self.zurich_set_freq(self.osc_num, new_freq)                   

                    change = new_freq - drive_freq
                    log.info(f'phase is HIGH, {median_phase_deviation} deg')
                    log.warning(f'DRIVE changed by {change}Hz')
                    log.info(
                        'Ringdown is {:}*{:.7g} s'.format(
                            self.num_tau, new_tau))


                elif median_phase_deviation < 0:
                    # set the frequency to be a little higher
                    target_phi = 0.5 * self.phase_limit
                    new_freq = fdrive_calculator(
                        target_phi, Q_infer, f0_infer)
                    self.zurich_set_freq(self.osc_num, new_freq)

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

    def zurich_set_amp(self, osc_num, amp):
        osc_path = f'/{self.zur_id}/sigouts/0/amplitudes/{osc_num}'
        self.daq.setDouble(osc_path, amp)

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

        # need to add offset for local time (DST-aware)
        time_axis_plot.plot.setAxisItems(
            {'bottom': DateAxisItem(utcOffset=LabView_t0 + get_local_utc_offset_seconds())})

        nyquist_plot = PlotWidget(
            name='Nyquist',
            columns=zurich_measure.DATA_COLUMNS,
            x_axis='X',
            y_axis='Y',
            # npts=zurich_measure.buffer_size,
        )
        nyquist_plot.plot.getViewBox().setAspectLocked(True, ratio=1.0)

        # Create two scatter plot items for bespoke Nyquist markers:
        # - older points: outline-only (transparent fill)
    # - recent points: filled with alpha varying linearly
    #   (most recent most opaque)
        self._nyquist_view = nyquist_plot.plot  # PlotItem
        self._nyquist_scatter_old = pg.ScatterPlotItem(
            size=6,
            pen=pg.mkPen(color=(0, 0, 0), width=1),
        )
        self._nyquist_scatter_recent = pg.ScatterPlotItem(
            size=8,
            pen=pg.mkPen(color=(0, 0, 0), width=1),
        )
        # Add to the plot (older points underneath recent)
        self._nyquist_view.addItem(self._nyquist_scatter_old)
        self._nyquist_view.addItem(self._nyquist_scatter_recent)

        # Connect the PlotWidget update signal to refresh the bespoke markers
    # `updated` is forwarded from PlotFrame and is emitted on the
    # refresh timer
        nyquist_plot.updated.connect(self._on_nyquist_updated)

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
                {'bottom': DateAxisItem(utcOffset=LabView_t0 + get_local_utc_offset_seconds())})

        self.setWindowTitle('Zurich fixed freq PLL')
        self.directory = r'D:/Data/Fall25-Summer26/TO pll tracking'
        self.file_input.filename_fixed = False

    def queue(self):
        directory = self.directory
        filename = unique_filename(directory, prefix=f'{self.filename}_')
        procedure = self.make_procedure()
        results = Results(procedure, filename)
        experiment = self.new_experiment(results)
        self.manager.queue(experiment)

    def update_nyquist_points(self, xs, ys, recent_count=100,
                              min_alpha=60, max_alpha=255,
                              recent_color=(0, 120, 255),
                              outline_color=(0, 0, 0)):
        """
        Update the Nyquist scatter:
         - points older than recent_count are drawn as outline-only
         - last recent_count points are filled with alpha varying linearly
           (most recent = max_alpha)
        xs, ys must be sequences in chronological order (oldest->newest).
        """
        xs = np.asarray(xs)
        ys = np.asarray(ys)
        N = len(xs)
        if N == 0:
            self._nyquist_scatter_old.setData([], [])
            self._nyquist_scatter_recent.setData([], [])
            return

        cutoff = max(0, N - recent_count)
        old_x, old_y = xs[:cutoff], ys[:cutoff]
        recent_x, recent_y = xs[cutoff:], ys[cutoff:]

        # older: outline only (transparent brush)
        if len(old_x):
            transparent_brushes = [pg.mkBrush(0, 0, 0, 0)] * len(old_x)
            pen_old = pg.mkPen(color=outline_color, width=1)
            self._nyquist_scatter_old.setData(x=old_x, y=old_y,
                                              brush=transparent_brushes,
                                              pen=pen_old,
                                              size=6)
        else:
            self._nyquist_scatter_old.setData([], [])

        # recent: per-point brushes with linearly varying alpha
        if len(recent_x):
            alphas = np.linspace(
                min_alpha, max_alpha, len(recent_x)
            ).astype(int)
            brushes = [pg.mkBrush(*recent_color, int(a)) for a in alphas]
            # optional: match pen alpha to fill alpha for nicer look
            pens = [pg.mkPen((outline_color[0], outline_color[1],
                              outline_color[2], int(a))) for a in alphas]
            self._nyquist_scatter_recent.setData(x=recent_x, y=recent_y,
                                                 brush=brushes,
                                                 pen=pens,
                                                 size=8)
        else:
            self._nyquist_scatter_recent.setData([], [])

    def _on_nyquist_updated(self):
        """Handler called on PlotWidget refresh; extracts the ResultsCurve data
        from the nyquist plot (if present) and updates bespoke markers.
        """
        # Find the first ResultsCurve-like item in the plot (ResultsCurve is a
        # PlotDataItem that the PlotWidget adds for the experiment data)
        x_data = None
        y_data = None
        for item in self._nyquist_view.items:
            # ResultsCurve inherits from PlotDataItem and stores `results` attr
            if (
                hasattr(item, 'results') and hasattr(item, 'x')
                and hasattr(item, 'y')
            ):
                try:
                    data = item.results.data
                    x_data = data[item.x]
                    y_data = data[item.y]
                except Exception:
                    # fallback: try to read from the plotted data directly
                    try:
                        xd, yd = item.getData()
                        x_data = xd
                        y_data = yd
                    except Exception:
                        x_data = None
                        y_data = None
                break

        if x_data is None or y_data is None:
            # nothing to update
            return

        # Ensure chronological order (Results.data is appended chronologically)
        self.update_nyquist_points(x_data, y_data)


if __name__ == '__main__':
    app = QtWidgets.QApplication([])
    window = zurich_graph()
    window.show()
    sys.exit(app.exec_())
