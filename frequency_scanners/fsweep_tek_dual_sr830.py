import logging
import sys
import time
import numpy as np
import concurrent.futures
from pymeasure.instruments.srs import SR830
from pymeasure.instruments.tektronix import AFG3152C
from pymeasure.display.Qt import QtWidgets
from pymeasure.display.windows import ManagedWindow
from pymeasure.experiment import Procedure, Results, unique_filename
from pymeasure.experiment import FloatParameter, Parameter

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


class TekDualSR830FSweep(Procedure):

    drive_amp = FloatParameter('Drive Amplitude (Vpp)', default=1)
    f_start = FloatParameter('Start Frequency (Hz)', default=22.9E3)
    f_final = FloatParameter('Stop Frequency (Hz)', default=23.1E3)
    f_step = FloatParameter('Frequency Step (Hz)', default=1)
    delay = FloatParameter('Delay (s)', default=3)
    sample_size = FloatParameter('Buffer/sample size per measurement',
                                 default=10)

    tek_id = Parameter('DS345 addr', default='2::16')
    sr830_id_1 = Parameter('SR830_1 addr.', default='0::12')
    sr830_id_2 = Parameter('SR830_2 addr.', default='2::9')

    params = [
        'drive_amp', 'f_start', 'f_final', 'f_step', 'delay', 'sample_size',
        'tek_id', 'sr830_id_1', 'sr830_id_2'
    ]

    DATA_COLUMNS = [
        'UTC', 'timestamp', 'f',
        'X_1', 'sigma_x1', 'Y_1', 'sigma_y2',
        'X_2', 'sigma_x2', 'Y_2', 'sigma_y2'
    ]

    def startup(self):
        log.info(
            'Starting frequency sweeper with Tektronix AFG drive,'
            'and measuring '
            'with two SR830s'
        )
        tek_full_address = f'GPIB{self.tek_id}::INSTR'
        sr830_1_full_address = f'GPIB{self.sr830_id_1}::INSTR'
        sr830_2_full_address = f'GPIB{self.sr830_id_2}::INSTR'

        log.info(
            f'Tek address: {tek_full_address},'
            f'SR830 1 address: {sr830_1_full_address}, '
            f'SR830 2 address: {sr830_2_full_address}'
        )

        self.afg = AFG3152C(tek_full_address)
        self.lockin_1 = SR830(sr830_1_full_address)
        self.lockin_2 = SR830(sr830_2_full_address)

        self.freq = np.arange(
            self.f_start,
            self.f_final + self.f_step, self.f_step)
        self.t_start = time.time()

        self.afg.ch1.frequency = self.freq[0]
        self.afg.ch1.amp_pp = self.drive_amp
        time.sleep(self.delay)

    def clear_lockin(self, sr: SR830):
        id_query = str(sr.id)
        id_example = 'Stanford_Research_Systems,SR830'
        if id_example not in id_query:
            sr.write('\n')
            sr.read()

    def execute(self):
        for i, f in enumerate(self.freq):
            self.afg.ch1.frequency = f
            time.sleep(self.delay)
            utc_time = time.time()
            ts = utc_time - self.t_start
            freq_meas = self.afg.ch1.frequency
            log.info(f'Tek frequency set to {freq_meas} Hz')

            log.info('Starting parallel measurement')
            retry = True
            while retry:
                t0_par = time.time()
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    duration = self.sample_size/self.lockin_1.sample_frequency
                    timeout_time = 1.1 * duration + 10
                    meas_1 = executor.submit(
                        self.lockin_1.buffer_measure_from_bytes,
                        self.sample_size, timeout_time
                    )
                    meas_2 = executor.submit(
                        self.lockin_2.buffer_measure_from_bytes,
                        self.sample_size, timeout_time
                    )

                    concurrent.futures.wait([meas_1, meas_2])

                    try:
                        log.info(
                            f'Buffer 1 size: {self.lockin_1.buffer_count}')
                        log.info(
                            f'Buffer 2 size: {self.lockin_2.buffer_count}')
                        x1_buff, y1_buff = meas_1.result()
                        x2_buff, y2_buff = meas_2.result()
                        [x1, sigma_x1] = [x1_buff.mean(), x1_buff.std()]
                        [y1, sigma_y1] = [y1_buff.mean(), y1_buff.std()]
                        [x2, sigma_x2] = [x2_buff.mean(), x2_buff.std()]
                        [y2, sigma_y2] = [y2_buff.mean(), y2_buff.std()]
                        retry = False
                    except Exception as e:
                        log.error(f'Parallel measurement failed: {e}')
                        log.info(f'Lockin_1 ID query: {self.lockin_1.id}')
                        log.info(f'Lockin_2 ID query: {self.lockin_2.id}')
                        log.info('Retrying measurement')
                        time.sleep(0.1)

                log.info(
                    f'Parallel measurement took {time.time() - t0_par:.3f}s')
                data = {
                    'UTC': utc_time,
                    'timestamp': ts,
                    'f': freq_meas,
                    'X_1': x1,
                    'sigma_x1': sigma_x1,
                    'Y_1': y1,
                    'sigma_y1': sigma_y1,
                    'X_2': x2,
                    'sigma_x2': sigma_x2,
                    'Y_2': y2,
                    'sigma_y2': sigma_y2,
                }

                self.emit('results', data)
                self.emit('progress', 100 * (i + 1) / len(self.freq))
                if self.should_stop():
                    log.warning('Caught the stop flag in the procedure')
                    break


class TekDualSR830Graph(ManagedWindow):

    def __init__(self):
        super().__init__(
            procedure_class=TekDualSR830FSweep,
            inputs=TekDualSR830FSweep.params,
            displays=TekDualSR830FSweep.params,
            x_axis='f',
            y_axis='X_1',
        )

        self.setWindowTitle('TekDualSR830FSweep Measurement Window')
        self.directory = r'data-files/tek-dual-sr830'

    def queue(self):
        directory = self.directory
        filename = unique_filename(directory, prefix='tek_dual_sr830')

        procedure = self.make_procedure()
        results = Results(procedure, filename)
        experiment = self.new_experiment(results)
        print(experiment)
        experiment.curve_list
        self.manager.queue(experiment=experiment)


def main():
    app = QtWidgets.QApplication(sys.argv)
    window = TekDualSR830Graph()
    window.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
