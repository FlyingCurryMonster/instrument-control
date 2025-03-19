import logging
import sys
import time
import numpy as np
from pymeasure.instruments.srs import SR830
from pymeasure.display.Qt import QtWidgets
from pymeasure.display.windows import ManagedWindow
from pymeasure.experiment import Procedure, Results, unique_filename
from pymeasure.experiment import FloatParameter, Parameter
from pymeasure.instruments.tektronix import AFG3152C

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


class TekSR830Sweep(Procedure):

    # drive_amp = FloatParameter('Drive Amplitude (Vpp)', default=1)
    f_start = FloatParameter('Start Frequency (Hz)', default=1324.410)
    f_final = FloatParameter('Stop Frequency (Hz)', default=1324.430)
    f_step = FloatParameter('Frequency Step (Hz)', default=2.5e-4)
    delay = FloatParameter('Delay (s)', default=330)
    
    tek_id = Parameter('Tek AFG addr', default='1::11')
    sr830_id = Parameter('SR830_2 addr.', default='2::9')
    comments = Parameter('Comments')

    params = [
        'f_start', 'f_final', 'f_step', 'delay',
        'tek_id', 'sr830_id', 'comments'
    ]

    DATA_COLUMNS = [
        'UTC', 'timestamp',
        'f',
        'X',
        'Y',
        'R',
        'phase',
        'V_drive'
    ]

    def startup(self):
        log.info('Starting frequency sweeper with Tektronix drive, '
                 'and measuring with SR830s')
        tek_full_address = f'GPIB{self.tek_id}::INSTR'
        sr830_full_address = f'GPIB{self.sr830_id}::INSTR'

        log.info(f'Tek address: {tek_full_address}, '
                 f'SR830  address: {sr830_full_address}')

        self.afg = AFG3152C(tek_full_address)
        self.lockin = SR830(sr830_full_address)

        self.freq = np.arange(
            self.f_start, self.f_final + self.f_step,
            self.f_step)
        self.t_start = time.time()

        self.afg.ch1.frequency = self.freq[0]
        # time.sleep(self.delay)

        time_constant = self.lockin.time_constant

        if self.delay < 10 * time_constant:
            log.warning('Your delay should be at least '
                        '10 times the time constant')
        else:
            log.info('Delay time is correctly >= 10 x the '
                     'measurement time constant')

        log.info(f'Lock-in TC: {time_constant}s')

        n_pts = int((self.f_final - self.f_start) / self.f_step)
        estimate_time = n_pts * self.delay
        log.info(f'Taking {n_pts} data points')
        log.info(f'Estimated measurement time is {estimate_time/60} minutes')

    def tek_signal_query(self, tek_afg: AFG3152C, phase_units='rad'):
        # need to include assertion error for phase_units
        assert phase_units in ['rad', 'deg']

        state = True
        while state:
            try:
                amp = tek_afg.ch1.amp_vpp
                freq = tek_afg.ch1.frequency
                if phase_units == 'rad':
                    phase = tek_afg.ch1.phase_rad
                else:
                    phase = tek_afg.ch1.phase_deg

                assert isinstance(amp, float), f"Amplitude type: {type(amp)}"
                assert isinstance(phase, float), f"Phase type: {type(phase)}"

                state = False

            except AssertionError as ae:
                log.warning(f"Assertion failed: {ae}. Retrying...")
                time.sleep(0.1)

            except Exception as e:
                log.warning(e)
                time.sleep(0.1)
        return amp, freq, phase

    def execute(self):
        for i, f in enumerate(self.freq):
            self.afg.ch1.frequency = f

            drive, freq_meas, _ = self.tek_signal_query(self.afg, 'deg')

            log.info(f'Tek frequency set to = {freq_meas} Hz')
            log.info(f'Sleeping for {self.delay}')
            time.sleep(self.delay)

            utc_time = time.time()
            ts = utc_time - self.t_start

            x, y = self.lockin.xy

            data = {
                'UTC': utc_time,
                'timestamp': ts,
                'f': freq_meas,
                'X': x,
                'Y': y,
                'R': np.sqrt(x**2 + y**2),
                'phase': np.rad2deg(np.arctan(y/x)),
                'V_drive': drive
            }

            self.emit('results', data)
            self.emit('progress', 100 * (i + 1) / len(self.freq))
            if self.should_stop():
                log.warning('Caught the stop flag in the procedure')
                break


class TekSR830Graph(ManagedWindow):

    def __init__(self):
        super().__init__(
            procedure_class=TekSR830Sweep,
            inputs=TekSR830Sweep.params,
            displays=TekSR830Sweep.params,
            x_axis='f',
            y_axis='X',
        )

        self.setWindowTitle('Tek and SR830 fsweep Measurement Window')
        self.directory = r'D:/Data/RNB-Spring2025/Tektronix and SR830 sweep'
        self.file_input.filename_fixed = False

    def queue(self):
        directory = self.directory
        filename = unique_filename(directory, prefix='tek_sr830')
        procedure = self.make_procedure()
        results = Results(procedure, filename)
        experiment = self.new_experiment(results)        
        self.manager.queue(experiment=experiment)


if __name__ == '__main__':
    app = QtWidgets.QApplication([])
    window = TekSR830Graph()
    window.show()
    sys.exit(app.exec_())
