import logging
from matplotlib import units
log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())
import sys
import time
import numpy as np
from pymeasure.instruments.srs import SR830
from pymeasure.display.Qt import QtWidgets
from pymeasure.display.windows import ManagedWindow
from pymeasure.experiment import Procedure, Results, unique_filename
from pymeasure.experiment import IntegerParameter, FloatParameter, Parameter
from pymeasure.instruments.tektronix import AFG3152C
from sr830_buffer import SR830Interface
import concurrent.futures

class tek_dual_sr830_fsweep(Procedure):

    drive_amp = FloatParameter('Drive Amplitude (Vpp)', default = 1)
    f_start = FloatParameter('start frequency (Hz)', default = 500)
    f_final = FloatParameter('stop frequency (Hz)', default = 1200)
    f_step = FloatParameter('Frequency Step (Hz)', default = 1)
    delay = FloatParameter('Delay (s)', default = 1)
    buffer_time = FloatParameter('Buffer measure time (s)', default = 3)

    tek_id = Parameter('DS345 addr', default = '2::16')
    sr830_id_1 = Parameter('SR830_1 addr.', default = '0::12')
    sr830_id_2 = Parameter('SR830_2 addr.', default = '2::9')
    
    params = [
        'drive_amp', 'f_start', 'f_final', 'f_step', 'delay',
        'ds345_id', 'sr830_id', 'sr830_id_2'
    ]

    DATA_COLUMNS = [
        'UTC', 'timestamp', 'f', 
        'X_1', 'sigma_x1', 'Y_1', 'sigma_y2', 
        'X_2', 'sigma_x2', 'Y_2', 'sigma_y2'
        ]

    def startup(self):
        log.info('Starting frequency sweeper with DS345 drive, and measuring with two SR830s')
        tek_full_address = f'GPIB{self.tek_id}::INSTR'
        sr830_1_full_address = f'GPIB{self.sr830_id_1}::INSTR'
        sr830_2_full_address = f'GPIB{self.sr830_id_2}::INSTR'


        log.info(f'Tek address:{tek_full_address}, SR830 1 address: {sr830_1_full_address}, SR830 2 address: {sr830_2_full_address}')

        self.fgen =AFG3152C(tek_full_address)
        self.lockin_1 = SR830Interface(sr830_1_full_address)
        self.lockin_2 = SR830Interface(sr830_2_full_address)

        self.freq = np.arange(self.f_start, self.f_final+self.f_step, self.f_step,)
        self.t_start = time.time()

        self.fgen.frequency = self.freq[0]
        self.fgen.amp_pp = self.drive_amp
        time.sleep(self.delay)
    def execute(self):
        for i, f in enumerate(self.freq):
            self.fgen.frequency = f
            time.sleep(self.delay)
            utc_time = time.time()
            ts = utc_time - self.t_start
            freq_meas = self.fgen.frequency
            log.info('Tek frequency set to ={} Hz'.format(freq_meas))

            ## parallel measurement
            with concurrent.futures.ThreadPoolExecutor() as executor:
                meas_1 = executor.submit(self.lockin_1.buffer_stats(self.buffer_time))
                meas_2 = executor.submit(self.lockin_2.buffer_stats(self.buffer_time))

                # wait for both methods to complete
                concurrent.futures.wait([meas_1, meas_2])


                [x1, sigma_x1], [y1, sigma_y1] = meas_1.result()
                [x2, sigma_x2], [y2, sigma_y2] = meas_2.result()

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
            self.emit('progress', 100*(i+1)/len(self.freq))
            if self.should_stop():
                log.warning('Caught the stop flag in the procedure')
                break    


class tek_dual_sr830_graph(ManagedWindow):
    
    def __init__(self):

        super().__init__(
            procedure_class=tek_dual_sr830_fsweep,
            inputs =tek_dual_sr830_fsweep.params,
            displays = tek_dual_sr830_fsweep.params,
            x_axis= 'f',
            y_axis = 'X_sr',
            directory_input = True
        )

        self.setWindowTitle('tek_dual_sr830_fsweep measurement window')
        self.directory =  r'data-files/tek-dual-sr830'

    def queue(self):
        directory = self.directory
        filename = unique_filename(directory, prefix = 'tek_dual_sr830')
        
        procedure = self.make_procedure()
        results = Results(procedure, filename)
        experiment = self.new_experiment(results)
        print(experiment)
        experiment.curve_list
        self.manager.queue(experiment=experiment)

def main():
    # rm = pyvisa.ResourceManager()
    # gpib_list = rm.list_resources()
    # print(pymeasure.__version__)
    
    app = QtWidgets.QApplication(sys.argv)
    window = tek_dual_sr830_graph()
    window.show()
    sys.exit(app.exec_()) 

if __name__ == '__main__':
    main()








