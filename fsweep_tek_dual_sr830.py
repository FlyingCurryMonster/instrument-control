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
import concurrent.futures

class tek_dual_sr830_fsweep(Procedure):

    drive_amp = FloatParameter('Drive Amplitude (Vpp)', default = 1)
    f_start = FloatParameter('start frequency (Hz)', default = 22.9E3)
    f_final = FloatParameter('stop frequency (Hz)', default = 23.1E3)
    f_step = FloatParameter('Frequency Step (Hz)', default = 1)
    delay = FloatParameter('Delay (s)', default = 3)
    sample_size = FloatParameter('Buffer/sample size per measurement', default = 10)

    tek_id = Parameter('DS345 addr', default = '2::16')
    sr830_id_1 = Parameter('SR830_1 addr.', default = '0::12')
    sr830_id_2 = Parameter('SR830_2 addr.', default = '2::9')
    
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
        log.info('Starting frequency sweeper with Tektronix AFG drive, and measuring with two SR830s')
        tek_full_address = f'GPIB{self.tek_id}::INSTR'
        sr830_1_full_address = f'GPIB{self.sr830_id_1}::INSTR'
        sr830_2_full_address = f'GPIB{self.sr830_id_2}::INSTR'

    
        log.info(f'Tek address:{tek_full_address}, SR830 1 address: {sr830_1_full_address}, SR830 2 address: {sr830_2_full_address}')

        self.afg =AFG3152C(tek_full_address)

        self.lockin_1 = SR830(sr830_1_full_address)
        self.lockin_2 = SR830(sr830_2_full_address)

        self.freq = np.arange(self.f_start, self.f_final+self.f_step, self.f_step,)
        self.t_start = time.time()

        self.afg.ch1.frequency = self.freq[0]
        self.afg.ch1.amp_pp = self.drive_amp
        time.sleep(self.delay)
    
    def clear_lockin(sr: SR830):
        id_query = str(sr.id)
        id_example = 'Stanford_Research_Systems,SR830'
        if id_example in id_query:
            return
        else: 
            sr.write('\n')
            sr.read()

    
    def execute(self):
        for i, f in enumerate(self.freq):
            self.afg.ch1.frequency = f
            time.sleep(self.delay)
            utc_time = time.time()
            ts = utc_time - self.t_start
            freq_meas = self.afg.ch1.frequency
            log.info('Tek frequency set to ={} Hz'.format(freq_meas))


            ## parallel measurement V2
            log.info('starting parallel measurement')
            retry = True
            while retry:
                t0_par = time.time()
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    timeout_time = 1.1*(self.sample_size/self.lockin_1.sample_frequency)+10
                    meas_1 = executor.submit(self.lockin_1.buffer_measure_from_bytes, self.sample_size, timeout_time)
                    meas_2 = executor.submit(self.lockin_2.buffer_measure_from_bytes, self.sample_size, timeout_time)

                    # wait for both methods to complete
                    concurrent.futures.wait([meas_1, meas_2])

                    try:
                        log.info(f'buffer 1 size is {self.lockin_1.buffer_count}')
                        log.info(f'buffer 2 size is {self.lockin_2.buffer_count}')
                        x1_buff, y1_buff = meas_1.result()
                        x2_buff, y2_buff = meas_2.result()
                        [x1, sigma_x1], [y1, sigma_y1] = [x1_buff.mean(), x1_buff.std()], [y1_buff.mean(), y1_buff.std()]
                        [x2, sigma_x2], [y2, sigma_y2] = [x2_buff.mean(), x2_buff.std()], [y2_buff.mean(), y2_buff.std()]
                        retry = False

                # [x1, sigma_x1], [y1, sigma_y1] = meas_1.result()
                # [x2, sigma_x2], [y2, sigma_y2] = meas_2.result()               
                    except Exception as e:
                        log.error(f'Parallel measurement failed: {e}')
                        log.info(f'Lockin_1 id query is {self.lockin_1.id}')
                        log.info(f'Lockin_2 id query is {self.lockin_2.id}')
                        log.info('Retrying measurement')
                        time.sleep(0.1)  


            # ## parallel measurement
            # log.info('starting parallel measurement')
            # t0_par = time.time()
            # with concurrent.futures.ThreadPoolExecutor() as executor:
            #     meas_1 = executor.submit(self.lockin_1.buffer_measure_from_bytes, self.sample_size)
            #     meas_2 = executor.submit(self.lockin_2.buffer_measure_from_bytes, self.sample_size)

            #     # wait for both methods to complete
            #     concurrent.futures.wait([meas_1, meas_2])

            #     log.info(f'buffer 1 size is {self.lockin_1.buffer_count}')
            #     log.info(f'buffer 2 size is {self.lockin_2.buffer_count}')

            #     try:
            #         x1_buff, y1_buff = meas_1.result()
            #         x2_buff, y2_buff = meas_2.result()
            #         [x1, sigma_x1], [y1, sigma_y1] = [x1_buff.mean(), x1_buff.std()], [y1_buff.mean(), y1_buff.std()]
            #         [x2, sigma_x2], [y2, sigma_y2] = [x2_buff.mean(), x2_buff.std()], [y2_buff.mean(), y2_buff.std()]
            #     except:
            #         log.error('Parallel measurement failed')
            #         log.info('Lockin_1 id query is ', self.lockin_1.id)
            #         log.info('Lockin_2 id query is ', self.lockin_2.id)
            #         # [x1, sigma_x1], [y1, sigma_y1] = meas_1.result()
            #         # [x2, sigma_x2], [y2, sigma_y2] = meas_2.result()   
            #         continue
                

            log.info('parallel measurement took {:.3f}s'.format(time.time()-t0_par))
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
            y_axis = 'X_1',
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








