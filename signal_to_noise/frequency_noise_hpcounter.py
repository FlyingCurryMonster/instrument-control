import logging
from matplotlib import units

# from sqlalchemy import Integer
log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())
import sys
sys.modules['cloudpickle'] = None  #THIS IS IMPORTANT TO INCLUDE IN CODE
from math import pi, sin, cos
import cmath
import time
from time import sleep
import pymeasure
import numpy as np
from pymeasure.log import console_log
from pymeasure.instruments import Instrument
from pymeasure.instruments.validators import strict_discrete_set, discreteTruncate
from pymeasure.instruments.srs import SR830
from pymeasure.instruments.tektronix import AFG3152C
from pymeasure.display.Qt import QtWidgets
from pymeasure.display.Qt import QtGui  
from pymeasure.display.windows import ManagedWindow
from pymeasure.display.widgets import PlotWidget, LogWidget
from pymeasure.experiment import Procedure, Results, unique_filename
from pymeasure.experiment import IntegerParameter, FloatParameter, Parameter

import pyvisa

class HP_53132A(Instrument):

    def __init__(self, resourceName, **kwargs):
        super().__init__(
            resourceName,
            'HP 53132A frequency'
    )
    # fetch = Instrument.measurement(
    #     "INIT\n:FETC?",
    #     '''Gets the numeric display'''
    # )

    # def long_TC_fetch(self, TC:float):
    #     self.write(command = ':INIT')
    #     if TC>10: sleep(TC*1.5)
    #     else: sleep(TC)
    #     return float(self.write(':FETC?')[0:-1])
    
    def long_TC_read(self, TC):
        return float(self.ask(command=':READ?', query_delay=TC)[0:-1])
    
    gate_time = Instrument.control(
        ':FREQ:ARM:STOP:TIM?', ':FREQ:ARM:STOP:TIM %g',
        docs = '',
        validator = discreteTruncate,
        values = np.append(
            np.arange(1E-3, 99.99E-3+0.01E-3, 0.01E-3), 
            np.arange(100E-3, 1E3+1E-3, 1E-3)
        ).tolist()
    )
    ##hello
    measure_mode = Instrument.control(
        ':FUNC?', ':FUNC "%s"',
        '''Property for the measurement mode''',
        validator = strict_discrete_set,
        values = ['FREQ', 'PER']
    )

param_list =['delay', 'TC', 'meas_time', 'instr_bus', 'instr_addr', 'file_comments']
class HP_counter_reader(Procedure):

    delay = FloatParameter('delay', units = 's', default = 0.2)
    TC = FloatParameter('Gate time', units = 's', default = 1)
    meas_time = FloatParameter('Measurement duration', units = 'mins', default = np.nan)
    instr_bus = IntegerParameter('instr. bus', default = 0)
    instr_addr = IntegerParameter('instr. address', default = 9)
    file_comments = Parameter('filename', default = '')
    
    DATA_COLUMNS = ['UTC', 'timestamp', 'period', 'freq']

    def startup(self):
        instr_full_addr = 'GPIB' + str(self.instr_bus) + '::' + str(self.instr_addr) + '::' + 'INSTR' 
        self.instr = HP_53132A(instr_full_addr)
        log.info('Starting counter reader {}'.format(self.instr.id))
        self.instr.gate_time = self.TC
        self.set_TC = self.instr.gate_time
        log.info('counter gate time set to={}'.format(self.set_TC))
        self.t0 = time.time()

    def execute(self):
        if self.meas_time != np.nan: stop_time = self.meas_time*60 + self.t0
        else:  stop_time = np.inf
        log.info('will stop measruement at {}'.format(stop_time))
        while time.time() < stop_time:
            period = self.instr.long_TC_read(self.set_TC)
            data = {
                'UTC': time.time(),
                'timestamp': time.time()-self.t0,
                'period': period,
                'freq': period**-1,
            }
            self.emit('results', data)
            self.emit('progress', (time.time()-self.t0)/(self.meas_time*60)*100)
            sleep(self.delay)

            if self.should_stop():
                log.warning('Caught the stop flag in the procedure')
                break

class counter_graph(ManagedWindow):
    
    def __init__(self):



        super().__init__(
            procedure_class=HP_counter_reader,
            inputs = param_list,
            displays = param_list,
            x_axis= 'timestamp',
            y_axis = 'freq',
            directory_input = True
        )
        
        self.setWindowTitle('counter graph')
        self.directory =  r'data-files/'

    def queue(self):
        directory = self.directory
        filename = unique_filename(directory, prefix = 'counter data lab computer')

        procedure = self.make_procedure()
        results = Results(procedure, filename)
        expriment = self.new_experiment(results)

        self.manager.queue(experiment=expriment)


if __name__ == '__main__':

    rm = pyvisa.ResourceManager()
    gpib_list = rm.list_resources()
    print(pymeasure.__version__)

    app = QtWidgets.QApplication(sys.argv)
    window = counter_graph()
    window.show()
    sys.exit(app.exec_())   



# class lockin_reader(Procedure):

#     delay = FloatParameter('delay', units = 's', default = 0.2)
#     sr830bus = IntegerParameter('SR830 bus', default = 2)
#     sr830addr = IntegerParameter('SR830 address', default = 9)
#     sr830_full_address = 'GPIB' + str(sr830bus) + '::' + str(sr830addr) + '::' + 'INSTR' 
#     DATA_COLUMNS = ['UTC', 'timestamp', 'X channel', 'Y channel']

#     def startup(self):
#         log.info('Starting lockin reader')
#         self.lockin_amp = SR830(self.sr830_full_address)
#         self.t0 = time.time()

#     def execute (self):
#         lockin = self.lockin_amp
#         while True:
#             data = {
#                 'UTC': time.time(),
#                 'timestamp': time.time()-self.t0,
#                 'X channel': lockin.x,
#                 'Y channel': lockin.y,
#             }
#             self.emit('results', data)
#             sleep(self.delay)

#             if self.should_stop():
#                 log.warning('Caught the stop flag in the procedure')
#                 break

# class lockin_graph(ManagedWindow):
    
#     def __init__(self):

#         param_list =['delay', 'sr830bus','sr830addr']

#         super().__init__(
#             procedure_class=lockin_reader,
#             inputs =['delay', 'sr830bus','sr830addr'],#param_list,
#             displays = ['delay', 'sr830bus','sr830addr'],
#             x_axis= 'timestamp',
#             y_axis = 'X channel',
#             directory_input = True
#         )

#         self.setWindowTitle('Lockin graph')
#         self.directory =  r'C:/Users/Jeevak/Documents/GitHub/SQUID/lockin graphs'

#     def queue(self):
#         directory = self.directory
#         filename = unique_filename(directory, prefix = 'Lockin data')

#         procedure = self.make_procedure()
#         results = Results(procedure, filename)
#         expriment = self.new_experiment(results)

#         self.manager.queue(experiment=expriment)
