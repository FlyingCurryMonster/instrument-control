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
from ds345 import DS345
from sr830_buffer import SR830Interface

class ds345_sr830_fsweep(Procedure):

    drive_amp = FloatParameter('Drive Amplitude (Vpp)', default = 1)
    f_start = FloatParameter('start frequency (Hz)', default = 500)
    f_final = FloatParameter('stop frequency (Hz)', default = 1200)
    f_step = FloatParameter('Frequency Step (Hz)', default = 1)
    delay = FloatParameter('Delay (s)', default = 1)
    buffer_time = FloatParameter('Buffer measure time (s)', default = 3)

    ds345_id = Parameter('DS345 addr', default = '2::16')
    sr830_id = Parameter('SR830 addr.', default = '2::25')
    
    params = [
        'drive_amp', 'f_start', 'f_final', 'f_step', 'delay',
        'ds345_id', 'sr830_id',
    ]

    DATA_COLUMNS = ['UTC', 'timestamp', 'f', 'X_sr', 'sigma_xsr', 'Y_sr', 'sigma_ysr']

    def startup(self):
        log.info('Starting frequency sweeper with DS345 drive, and measuring with  SR830')
        ds345_full_address = f'GPIB{self.ds345_id}::INSTR'
        sr830_full_address = f'GPIB{self.sr830_id}::INSTR'
        # sr830_full_address = 'GPIB{}::INSTR'.format(self.sr830_id)

        log.info(f'DS345 address:{ds345_full_address}, SR830 address: {sr830_full_address}')

        self.fgen =DS345(ds345_full_address)
        # self.sr830 = SR830(sr830_full_address)
        self.sr830 = SR830Interface(sr830_full_address)
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
            log.info('DS345 frequency set to ={} Hz'.format(freq_meas))
            [xsr, sigma_xsr], [ysr, sigma_ysr] = self.sr830.buffer_stats(self.buffer_time)
            data = {
                'UTC': utc_time,
                'timestamp': ts,
                'f': freq_meas,
                'X_sr': xsr,
                'sigma_xsr': sigma_xsr,
                'Y_sr': ysr,
                'sigma_ysr': sigma_ysr,
            }
        
            self.emit('results', data)
            self.emit('progress', 100*(i+1)/len(self.freq))
            if self.should_stop():
                log.warning('Caught the stop flag in the procedure')
                break    


class ds345_sr830_graph(ManagedWindow):
    
    def __init__(self):

        super().__init__(
            procedure_class=ds345_sr830_fsweep,
            inputs =ds345_sr830_fsweep.params,
            displays = ds345_sr830_fsweep.params,
            x_axis= 'f',
            y_axis = 'X_sr',
            directory_input = True
        )

        self.setWindowTitle('DS345-SR830 measurement window')
        self.directory =  r'data-files/ds345-sr830'

    def queue(self):
        directory = self.directory
        filename = unique_filename(directory, prefix = 'ds345-sr830')
        
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
    window = ds345_sr830_graph()
    window.show()
    sys.exit(app.exec_()) 

if __name__ == '__main__':
    main()








