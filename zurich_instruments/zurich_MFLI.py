import zhinst.core as zi
import numpy as np
import time

class zi_MFLI:

    def __init__(self,
                device_id,
                interface = '1GbE',
                server_host = 'localhost',
                server_port = 8004,
                api_level = 6,):

        self.device_id = device_id

        self.daq = zi.ziDAQServer(server_host, server_port, api_level)
        self.daq.connectDevice(device_id, interface)
        
    def path(self, basepath:str, demod_num:int):
        return f'/{self.device_id}/{basepath}/{demod_num}/'
    

    def get_XY(self, demod:int = 0):
        _path = f'/{self.device_id}/demods/{demod}/sample'
        resp = self.daq.getSample(_path)
        return [resp['x'], resp['y']]
    
    def get_freq(self, demod:int = 0):
        _path = f'/{self.device_id}/demods/{demod}/'
        resp = self.daq.getSample(_path+'sample')
        return resp['frequency']

    def set_freq(self, freq, demod:int = 0):
        _path = f'/{self.device_id}/demods/{demod}/freq'
        self.daq.setDouble(_path, freq)

    def set_harmonic(self, harm:int, demod:int = 0):
        _path = f'/{self.device_id}/demods/{demod}/harmonic'
        self.daq.setInt(_path, harm)
    
    def set_phase(self, theta:float, demod:int=0):  #Sets phase in degrees
        _path = f'/{self.device_id}/demods/{demod}/phaseshift'
        self.daq.setDouble(_path, theta)
    
    def out_on(self, demod:int=0):
        _path = f'/{self.device_id}/sigouts/0/enables/{demod}'
        self.daq.setInt(_path, 1)

    def out_off(self, demod:int=0):
        _path = f'/{self.device_id}/sigouts/0/enables/{demod}'
        self.daq.setInt(_path, 0)
    
    def set_amp(self, voltage:float, demod:int =0): #peak to peak
        _path = f'/{self.device_id}/sigouts/0/amplitudes/{demod}'
        self.daq.setDouble(_path, voltage)
    
    def sigout_range(self, range:int,):
        ranges = [1E-2, 1E-1, 1, 10,]
        _path = f'/{self.device_id}/sigouts/0/range'
        try:
            self.daq.setDouble(_path, range)
            if range not in ranges: raise ValueError
        except ValueError:
            print('The possible ranges are' +str(ranges))
            set_range = self.daq.getDouble(_path)
            print('Range has been set to {:}'.format(set_range))
    
    def set_TC(self, TC:float, demod:int =0):
        _path = f'/{self.device_id}/demods/{demod}/timeconstant'
        self.daq.setDouble(_path, TC)
        return self.get_TC(demod)

    def get_TC(self, demod:int=0):
        _path = f'/{self.device_id}/demods/{demod}/timeconstant'
        return self.daq.getDouble(_path)

