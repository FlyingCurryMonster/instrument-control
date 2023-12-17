import logging
from matplotlib import units
log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())
import sys
# sys.modules['cloudpickle'] = None  #THIS IS IMPORTANT TO INCLUDE IN CODE
import time
from datetime import datetime as dt
import pymeasure
import pyvisa
import numpy as np
from pymeasure.instruments import Instrument
from pymeasure.instruments.validators import strict_discrete_set, discreteTruncate
from pymeasure.instruments.srs import SR830
from pymeasure.instruments.tektronix import AFG3152C
from pymeasure.display.Qt import QtWidgets
from pymeasure.display.Qt import QtGui  
from pymeasure.display.windows import ManagedWindow
from pymeasure.display.widgets import PlotWidget, LogWidget
from pymeasure.experiment import Procedure, Results, unique_filename
from pymeasure.experiment import IntegerParameter, FloatParameter, Parameter, BooleanParameter

import zhinst.core

class zurich_pll_noise(Procedure):

    ringdown = FloatParameter('ringdown delay', units = 's', default = 3)
    measurement_time = FloatParameter('measurement time', units = 'min', default = 60)
    
    # sr830addr = Parameter('SR830 addr.', default = '2::9')
    device_id = Parameter('Zurich addr.', default = 'dev4934')
    comments = Parameter('Comment', default = '')

    params = [
        'ringdown', 'measurement_time', 
        'device_id','comments',
        ]
    DATA_COLUMNS = ['utc', 'timestamp', 'f_zur', 'X', 'Y', ]

    def startup(self):
        log.info('Starting frequency sweeper with zurich')
        
        self.daq = zhinst.core.ziDAQServer('localhost', 8004, 6)
        self.daq.connectDevice(self.device_id, interface = '1GbE')
        self.daq.set(f"/{self.device_id}/demods/0/enable", 1)

        # self.daq.setDouble('/dev4934/sigouts/0/amplitudes/0', self.drive_v)

        # self.daq.setDouble('/dev4934/demods/0/timeconstant', self.zur_TC)
        self.demod_path = f"/{self.device_id}/demods/0/sample"

        log.info('drive set to {:.3f}'.format(self.daq.getDouble('/dev4934/sigouts/0/amplitudes/0')))
        time.sleep(self.ringdown)
        log.info('Zurich TC set to {:.3f}'.format(self.daq.getDouble('/dev4934/demods/0/timeconstant')))


        self.t_meas_start = time.time()
        self.t_meas_end = self.t_meas_start + self.measurement_time*60
    def execute (self):
        
        while time.time() < self.t_meas_end:
            self.measure_and_record()
            if self.should_stop(): break;log.warning('Caught the stop flag in the procedure')
        # if self.should_stop():break; log.warning('Caught the stop flag in the procedure')

    def measure_and_record(self,):
        # log.info('measuring')
        X,Y, zur_f = self.zurich_sample_read()
        data = {
            'utc': time.time(),
            'timestamp': time.time()-self.t_meas_start,
            'f_zur': zur_f,
            'X': X,
            'Y': Y,
        }
        self.emit('results', data)
        self.emit('progress', 
            (time.time()-self.t_meas_start)/(self.t_meas_end-self.t_meas_start)*100
        )
    
    def zurich_sample_read(self):
        resp = self.daq.getSample(self.demod_path)
        _x, _y, _f = resp['x'][0], resp['y'][0], resp['frequency'][0]
        return _x, _y, _f
    
    
    def zurich_autorange(self):
        SENSITIVITIES = [1e-3, 3e-3, 1e-2, 3e-2, 0.1, 0.3, 1, 3]
        
        self.daq.setInt(f'/{self.device_id}/sigins/0/autorange', 1)



class zurich_graph(ManagedWindow):
    
    def __init__(self):

        super().__init__(
            procedure_class=zurich_pll_noise,
            inputs =zurich_pll_noise.params,
            displays = zurich_pll_noise.params,
            x_axis= 'timestamp',
            y_axis = 'X',
            directory_input = True
        )

        self.setWindowTitle('Zurich PLL noise')
        self.directory =  r'data-files/zurich-PLL-noise'

    def queue(self):
        directory = self.directory
        filename = unique_filename(directory, prefix = 'zurich-PID-noise')
        
        procedure = self.make_procedure()
        results = Results(procedure, filename)
        experiment = self.new_experiment(results)
        print(experiment)
        experiment.curve_list
        self.manager.queue(experiment=experiment)

if __name__=='__main__':
    
    app = QtWidgets.QApplication(sys.argv)
    window = zurich_graph()
    window.show()
    sys.exit(app.exec_()) 



