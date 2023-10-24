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


class zurich_sr830_fsweep(Procedure):

    drive_amp = FloatParameter('Drive Amplitude (Vpp)', default = 1)
    f_start = FloatParameter('start frequency (Hz)', default = 500)
    f_final = FloatParameter('stop frequency (Hz)', default = 1200)
    f_step = FloatParameter('Frequency Step (Hz)', default = 1)
    delay = FloatParameter('Delay (s)', default = 1)

    zur_id = Parameter('Zurich addr.', default = 'dev4934')
    sr830_id = Parameter('SR830 addr.', default = '2::25')
    
    params = [
        'drive_amp', 'f_start', 'f_final', 'f_step', 'delay',
        'zur_id', 'sr830_id',
    ]

    DATA_COLUMNS = ['UTC', 'timestamp', 'f', 'X_zur', 'Y_zur', 'X_sr', 'Y_sr']

    def startup(self):
        log.info('Starting frequency sweeper with Zurich drive, and measuring with Zurich and SR830')
        self.daq = zhinst.core.ziDAQServer('localhost', 8004, 6)
        self.daq.connectDevice(self.zur_id, interface = '1GbE')
        self.daq.set(f"/{self.zur_id}/demods/0/enable", 1)
        self.demod_path = f"/{self.zur_id}/demods/0/sample"

        sr830_full_address = 'GPIB{}::INSTR'.format(self.sr830_id)
        log.info(sr830_full_address)
        self.sr830 = SR830(sr830_full_address)

        self.freq = np.arange(self.f_start, self.f_final+self.f_step, self.f_step,)
        self.t_start = time.time()

        self.zurich_set_freq(self.freq[0])
        time.sleep(self.delay*5)
    def execute(self):
        for i, f in enumerate(self.freq):
            self.zurich_set_freq(f)
            time.sleep(self.delay)
            utc_time = time.time()
            ts = utc_time - self.t_start
            xzur, yzur, freq_meas = self.zurich_sample_read()
            log.info('Zurich frequency set to ={}'.format(freq_meas))
            xsr, ysr = self.sr830.xy
            data = {
                'UTC': utc_time,
                'timestamp': ts,
                'f': freq_meas,
                'X_zur': xzur,
                'Y_zur': yzur,
                'X_sr': xsr,
                'Y_sr': ysr,
            }
        
            self.emit('results', data)
            self.emit('progress', 100*(i+1)/len(self.freq))
            if self.should_stop():
                log.warning('Caught the stop flag in the procedure')
                break    

    
    def zurich_sample_read(self):
        resp = self.daq.getSample(self.demod_path)
        _x, _y, _f = resp['x'][0], resp['y'][0], resp['frequency'][0]
        return _x, _y, _f
    def zurich_set_freq(self, _f, osc =0):
        self.daq.setDouble('{}/oscs/{}/freq'.format(self.zur_id, osc), _f)


class zurich_graph(ManagedWindow):
    
    def __init__(self):

        super().__init__(
            procedure_class=zurich_sr830_fsweep,
            inputs =zurich_sr830_fsweep.params,
            displays = zurich_sr830_fsweep.params,
            x_axis= 'f',
            y_axis = 'X_zur',
            directory_input = True
        )

        self.setWindowTitle('Zurich-SR830 measurement window')
        self.directory =  r'data-files/zurich-sr830'

    def queue(self):
        directory = self.directory
        filename = unique_filename(directory, prefix = 'TO_magnet')
        
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

if __name__ == '__main__':
    main()

# class fork_freq_sweep(Procedure):

#     ringdown = FloatParameter('ringdown delay', units = 's', default = 3)
#     exposure_time = FloatParameter('exposure time', units = 'min', default = 60)
#     gate_time = FloatParameter('counter gate time', units = 's', default = 1)
    
#     # sr830addr = Parameter('SR830 addr.', default = '2::9')
#     device_id = Parameter('Zurich addr.', default = 'dev4934')
#     # drive_v = FloatParameter('Zurich drive V', default = 0.1, units = 'V')
#     # zur_TC = FloatParameter('Zurich TC', default = 10, units = 's')
#     # afgaddr = Parameter('AFG3152 addr.', default = '2::11')
#     countaddr = Parameter('HP counter addr.', default = '2::3')
#     comments = Parameter('Comment', default = '')
#     # tek_amp = FloatParameter('Tek amp', units = 'V', default = 0.1)

#     # fstart = FloatParameter('f initial', units = 'Hz', default = 32760)
#     # fend = FloatParameter('f final', units = 'Hz', default = 32770)
#     # coarse_res = FloatParameter('coarse resolution', units = 'Hz', default = 0.1)
#     # fine_scan = BooleanParameter('fine scan switch', default = False)
#     # fine_f_start = FloatParameter('fine f initial', units = 'Hz', default = 32764)
#     # fine_f_final = FloatParameter('fine f final', units = 'Hz', default = 32766)
#     # fine_res = FloatParameter('fine resolution', units = 'Hz', default = 0.01)
#     params = [
#         'ringdown', 'exposure_time', 'gate_time', 
#         # 'drive_v', 'zur_TC', 
#         'comments',
#         # 'sr830addr', 'afgaddr', 'countaddr',  
#         # 'tek_amp', 'fstart', 'fend', 'coarse_res',
#         # 'fine_scan', 'fine_f_start', 'fine_f_final', 'fine_res'
#         ]
#     DATA_COLUMNS = ['date', 'timestamp', 'f', 'X', 'Y', 'f_zur']

#     def startup(self):
#         log.info('Starting frequency sweeper with zurich')
        
#         self.daq = zhinst.core.ziDAQServer('localhost', 8004, 6)
#         self.daq.connectDevice(self.device_id, interface = '1GbE')
#         self.daq.set(f"/{self.device_id}/demods/0/enable", 1)

#         # self.daq.setDouble('/dev4934/sigouts/0/amplitudes/0', self.drive_v)

#         # self.daq.setDouble('/dev4934/demods/0/timeconstant', self.zur_TC)
#         self.demod_path = f"/{self.device_id}/demods/0/sample"

#         log.info('drive set to {:.3f}'.format(self.daq.getDouble('/dev4934/sigouts/0/amplitudes/0')))
#         log.info('Zurich TC set to {:.3f}'.format(self.daq.getDouble('/dev4934/demods/0/timeconstant')))
#         ##################################################
#         # self.afg = AFG3152C('GPIB'+self.afgaddr+'::INSTR')
#         self.counter = HP_53132A('GPIB'+self.countaddr+'::INSTR')
#         log.info(self.counter.id)

#         self.counter.gate_time = self.gate_time


#         self.t_meas_start = time.time()
#         self.t_meas_end = self.t_meas_start + self.exposure_time*60
#         # log.info('measurement will take' + str(self.estimator(
#         #     self.t_meas_start, 
#         #     self.freq_sample, self.ringdown, 
#         #     self.exposure_time)[1]/60
#         #     ) + ' minutes')
#     def execute (self):
        
#         while time.time() < self.t_meas_end:
#             self.measure_and_record()
#             if self.should_stop(): break;log.warning('Caught the stop flag in the procedure')
#         # if self.should_stop():break; log.warning('Caught the stop flag in the procedure')

#     def measure_and_record(self,):
#         # log.info('measuring')
#         period = self.counter.long_TC_read(self.gate_time)
#         X,Y, zur_f = self.zurich_sample_read()

#         data = {
#             'Date': dt.now().strftime('%m/%d/%Y %H:%M:%S'),
#             'timestamp': time.time()-self.t_meas_start,
#             'f_zur': zur_f,
#             'f': period**-1,
#             'X': X,
#             'Y': Y,
#         }
#         self.emit('results', data)
#         self.emit('progress', 
#             (time.time()-self.t_meas_start)/(self.exposure_time*60)
#         )
    
#     def zurich_sample_read(self):
#         resp = self.daq.getSample(self.demod_path)
#         _x, _y, _f = resp['x'][0], resp['y'][0], resp['frequency'][0]
#         return _x, _y, _f
    
#     # def zurich_freq_read(self):
#     #     resp = self.daq.get()
    
#     def zurich_autorange(self):
#         SENSITIVITIES = [1e-3, 3e-3, 1e-2, 3e-2, 0.1, 0.3, 1, 3]
        
#         self.daq.setInt(f'/{self.device_id}/sigins/0/autorange', 1)

#     def freq_sampler(self, ):
#         fsample = np.arange(self.fstart, self.fend+self.coarse_res, self.coarse_res)
#         if self.fine_scan:
#             log.info('Fine scan selected')
#             fine_fsample = np.arange(
#                 self.fine_f_start, 
#                 self.fine_f_final + self.fine_res,
#                 self.fine_res
#             )
#             fsample = np.sort(np.append(fsample, fine_fsample))
    
#         return fsample

#     def estimator(self, utcstart, samples, delay, time_constant):
#         _t = delay+time_constant
#         time_length = len(samples)*_t
#         return (time.time()-utcstart)/time_length*100, time_length 
            

#     def autorange(self, lockin: SR830, signal, initial_sensitivity):
#         log.info('beginning autorange')
#         log.info('current sensitivity :' + str(initial_sensitivity))
#         log.info('signal size = '+ str(signal))
#         if signal < 0.8*initial_sensitivity and signal >0.2*initial_sensitivity: 
#             opt_sens = initial_sensitivity
#             log.info('sensitivity unchanged')
#         else:
#             log.info('sensitivity changing...')
#             sens = [s for s in SR830.SENSITIVITIES if 0.8*s > signal]
#             if not sens: #checks if the sens array is empty.  This will happen if signal is a few hundred mV's
#                 opt_sens = 1
#             else:
#                 opt_sens = sens[0]
#             log.info('optimal sensitivity is ' + str(opt_sens))
#             lockin.sensitivity = opt_sens
#             self.current_sens = lockin.sensitivity
#             time.sleep(0.1)
#             log.info('sensitivity has bee set to :' + str(self.current_sens))
#         return opt_sens


# class HP_53132A(Instrument):

#     def __init__(self, resourceName, **kwargs):
#         super().__init__(
#             resourceName,
#             'HP 53132A frequency'
#     )
    
#     def long_TC_read(self, TC):
#         return float(self.ask(command=':READ?', query_delay=TC)[0:-1])
    
#     gate_time = Instrument.control(
#         ':FREQ:ARM:STOP:TIM?', ':FREQ:ARM:STOP:TIM %g',
#         docs = '',
#         validator = discreteTruncate,
#         values = np.append(
#             np.arange(1E-3, 99.99E-3+0.01E-3, 0.01E-3), 
#             np.arange(100E-3, 1E3+1E-3, 1E-3)
#         ).tolist()
#     )

#     measure_mode = Instrument.control(
#         ':FUNC?', ':FUNC "%s"',
#         '''Property for the measurement mode''',
#         validator = strict_discrete_set,
#         values = ['FREQ', 'PER']
#     )






