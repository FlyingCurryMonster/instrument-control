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
    measurement_time = FloatParameter('exposure time', units = 'min', default = 60)
    
    # sr830addr = Parameter('SR830 addr.', default = '2::9')
    device_id = Parameter('Zurich addr.', default = 'dev4934')
    comments = Parameter('Comment', default = '')

    params = [
        'ringdown', 'exposure_time', 
        'device_id','comments',
        ]
    DATA_COLUMNS = ['date', 'timestamp', 'f_zur', 'X', 'Y', ]

    def startup(self):
        log.info('Starting frequency sweeper with zurich')
        
        self.daq = zhinst.core.ziDAQServer('localhost', 8004, 6)
        self.daq.connectDevice(self.device_id, interface = '1GbE')
        self.daq.set(f"/{self.device_id}/demods/0/enable", 1)

        # self.daq.setDouble('/dev4934/sigouts/0/amplitudes/0', self.drive_v)

        # self.daq.setDouble('/dev4934/demods/0/timeconstant', self.zur_TC)
        self.demod_path = f"/{self.device_id}/demods/0/sample"

        log.info('drive set to {:.3f}'.format(self.daq.getDouble('/dev4934/sigouts/0/amplitudes/0')))
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
            'Date': dt.now().strftime('%m/%d/%Y %H:%M:%S'),
            'timestamp': time.time()-self.t_meas_start,
            'f_zur': zur_f,
            'X': X,
            'Y': Y,
        }
        self.emit('results', data)
        self.emit('progress', 
            (time.time()-self.t_meas_start)/(self.t_meas_end-self.t_meas_start)/100
        )
    
    def zurich_sample_read(self):
        resp = self.daq.getSample(self.demod_path)
        _x, _y, _f = resp['x'][0], resp['y'][0], resp['frequency'][0]
        return _x, _y, _f
    
    
    def zurich_autorange(self):
        SENSITIVITIES = [1e-3, 3e-3, 1e-2, 3e-2, 0.1, 0.3, 1, 3]
        
        self.daq.setInt(f'/{self.device_id}/sigins/0/autorange', 1)

    def freq_sampler(self, ):
        fsample = np.arange(self.fstart, self.fend+self.coarse_res, self.coarse_res)
        if self.fine_scan:
            log.info('Fine scan selected')
            fine_fsample = np.arange(
                self.fine_f_start, 
                self.fine_f_final + self.fine_res,
                self.fine_res
            )
            fsample = np.sort(np.append(fsample, fine_fsample))
    
        return fsample

    def estimator(self, utcstart, samples, delay, time_constant):
        _t = delay+time_constant
        time_length = len(samples)*_t
        return (time.time()-utcstart)/time_length*100, time_length 
            

    def autorange(self, lockin: SR830, signal, initial_sensitivity):
        log.info('beginning autorange')
        log.info('current sensitivity :' + str(initial_sensitivity))
        log.info('signal size = '+ str(signal))
        if signal < 0.8*initial_sensitivity and signal >0.2*initial_sensitivity: 
            opt_sens = initial_sensitivity
            log.info('sensitivity unchanged')
        else:
            log.info('sensitivity changing...')
            sens = [s for s in SR830.SENSITIVITIES if 0.8*s > signal]
            if not sens: #checks if the sens array is empty.  This will happen if signal is a few hundred mV's
                opt_sens = 1
            else:
                opt_sens = sens[0]
            log.info('optimal sensitivity is ' + str(opt_sens))
            lockin.sensitivity = opt_sens
            self.current_sens = lockin.sensitivity
            time.sleep(0.1)
            log.info('sensitivity has bee set to :' + str(self.current_sens))
        return opt_sens




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

def main():
    # rm = pyvisa.ResourceManager()
    # gpib_list = rm.list_resources()
    # print(pymeasure.__version__)
    
    app = QtWidgets.QApplication(sys.argv)
    window = zurich_graph()
    window.show()
    sys.exit(app.exec_()) 



