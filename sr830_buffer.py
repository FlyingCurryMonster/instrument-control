import sr830_buffer as sr830_buffer
import numpy as np
from pyvisa import ResourceManager
from pyvisa.resources import MessageBasedResource
from time import sleep
# result =sr830_bufferA.square(3)
# print(result)

class SR830Interface:
    def __init__(self, address):
        # Implement your instrument initialization logic here
        # Example: open a connection to the instrument
        self.rm = ResourceManager()
        self.instrument = self.rm.open_resource(address)

    def buffer_stats(self, time):
        xbuf, ybuf = self.buffer_measure(time =time)
        return [np.mean(xbuf), np.std(xbuf)], [np.mean(ybuf), np.std(ybuf)]

    def buffer_measure(self, time):
        self.reset_buffer()
        self.start_buffer()
        sleep(time)
        self.pause_buffer()
        x, y =self.read_buffer_bytes()
        return x, y


    def buffer_bytes_convert(self, buffer):
        mant = np.array(list(buffer[0::4])) + np.array(list(buffer[1::4]))*2**8
        exp = np.array(list(buffer[2::4]))
        return mant*np.power(np.ones(shape = exp.shape)*2, exp-124)

    def read_buffer_bytes(self):
        buffer_size = self.n_buff()
        self.instrument.write(f'TRCL?1, 0, {buffer_size}')
        x_bytes = self.instrument.read_raw()
        self.instrument.write(f'TRCL?2, 0, {buffer_size}')
        y_bytes = self.instrument.read_raw()

        x_buffer, y_buffer = self.buffer_bytes_convert(x_bytes), self.buffer_bytes_convert(y_bytes)
        return x_buffer, y_buffer
    
    def read_buffer_ascii(self):
        buffer_size = self.n_buff()
        x_buffer = self.instrument.query_ascii_values(f"TRCA? 1, 0, {buffer_size}")
        y_buffer = self.instrument.query_ascii_values(f"TRCA? 2, 0, {buffer_size}")
        return x_buffer, y_buffer

    def start_buffer(self):
        self.instrument.write("STRT")

    def pause_buffer(self):
        self.instrument.write("PAUS")

    def reset_buffer(self):
        self.instrument.write("REST")

    def snap(self):
        return self.instrument.query('SNAP? 1,2')
    
    def n_buff(self):
        return float(self.instrument.query('SPTS?'))