import zhinst.core as zi
import numpy as np
import time



class zi_MFLI:

    def __init__(self,
                device_id,
                interface = '1GbE',
                server_host = 'localhost',
                # sever_host = '192.168.77.2',
                server_port = 8004,
                api_level = 6,):


        self.daq = zi.ziDAQServer(server_host, server_port, api_level)
        self.instr.connectDevice(device_id, interface)
        self.spath = f'/{device_id}/demods/0/sample'

def get_X():
    return
def get_Y():
    return
def get_R():
    return
def get_theta():
    return
def get_TC():
    return
def get_filter():
    return
def set_TC():
    return
def set_filter():
    return 