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

class lockin_reader(Procedure):

    delay = FloatParameter('delay', units = 's', default = 0.2)
    sr830bus = IntegerParameter('SR830 bus', default = 0)
    sr830addr = IntegerParameter('SR830 address', default = 12)
    DATA_COLUMNS = ['UTC', 'timestamp', 'X channel', 'Y channel']

    def startup(self):
        # sr830_full_address = 'GPIB' + str(self.sr830bus) + '::' + str(self.sr830addr) + '::' + 'INSTR' 
        sr830_full_address = 'GPIB{x}::{y}::INSTR'.format(x=self.sr830bus, y=self.sr830addr)
        log.info(sr830_full_address)
        log.info('Starting lockin reader')
        self.lockin_amp = SR830(sr830_full_address)
        self.t0 = time.time()

    def execute (self):
        lockin = self.lockin_amp
        while True:
            x, y = lockin.xy
            data = {
                'UTC': time.time(),
                'timestamp': time.time()-self.t0,
                'X channel': x,
                'Y channel': y,
            }
            self.emit('results', data)
            sleep(self.delay)

            if self.should_stop():
                log.warning('Caught the stop flag in the procedure')
                break

class lockin_graph(ManagedWindow):
    
    def __init__(self):

        param_list =['delay', 'sr830bus','sr830addr']

        super().__init__(
            procedure_class=lockin_reader,
            inputs =['delay', 'sr830bus','sr830addr'],#param_list,
            displays = ['delay', 'sr830bus','sr830addr'],
            x_axis= 'timestamp',
            y_axis = 'X channel',
            directory_input = True
        )

        self.setWindowTitle('Lockin graph')
        self.directory =  r'data-files/misc'

    def queue(self):
        directory = self.directory
        filename = unique_filename(directory, prefix = 'Lockin data')

        procedure = self.make_procedure()
        results = Results(procedure, filename)
        expriment = self.new_experiment(results)

        self.manager.queue(experiment=expriment)

if __name__ == '__main__':

    rm = pyvisa.ResourceManager()
    gpib_list = rm.list_resources()
    print(pymeasure.__version__)

    app = QtWidgets.QApplication(sys.argv)
    window = lockin_graph()
    window.show()
    sys.exit(app.exec_())   