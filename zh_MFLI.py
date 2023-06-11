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
        

    def get_XY(self, demod:int = 0):
        demod_path = f'/{self.device_id}/demods/{demod}/'
        resp = self.daq.getSample(demod_path+'sample')
        return [resp['x'], resp['y']]
    
    def get_freq(self, demod:int = 0):
        demod_path = f'/{self.device_id}/demods/{demod}/'
        resp = self.daq.getSample(demod_path+'sample')
        return resp['frequency']

    def set_freq(self, freq, demod:int = 0):
        demod_path = f'/{self.device_id}/demods/{demod}/'
        self.daq.setDouble('/dev4934/oscs/0/freq', freq)

    def set_harmonic(self, harm:int, demod:int = 0):
        demod_path = f'/{self.device_id}/demods/{demod}/'
        self.daq.setInt(demod_path+'harmonic', harm)
    
    def set_phase(self, theta:float, demod:int=0):  #Sets phase in degrees
        demod_path = f'/{self.device_id}/demods/{demod}/'
        self.daq.setDouble(demod_path+'phaseshift', theta)
    
    def out_on(self, demod:int=0):
        sigout_path = f'/{self.device_id}/sigouts/0/enables/{demod}'
        self.daq.setInt(sigout_path, 1)
    def out_off(self, demod:int=0):
        sigout_path = f'/{self.device_id}/sigouts/0/enables/{demod}'
        self.daq.setInt(sigout_path, 0)
    
    def set_amp(self, voltage:float, demod:int =0): #peak to peak
        sigout_path = f'/{self.device_id}/sigouts/0/amplitudes/{demod}'
        self.daq.setDouble(sigout_path, voltage)
    
    
    
        
