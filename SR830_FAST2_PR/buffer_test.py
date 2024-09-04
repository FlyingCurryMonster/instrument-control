import numpy as np
import matplotlib.pyplot as plt
from importlib import reload
import time
from pyvisa.constants import BufferOperation
import pyvisa.constants
import pymeasure.instruments.srs as srs

samp_freq = 512
count_mult = 5
def custom_method(fast:bool, count:int):
    # My custom method
    lockin1 = srs.SR830('GPIB2::9::INSTR')
    print('starting the fast method')
    print(lockin1.id)
    print(lockin1.x, lockin1.y)

    lockin1.sample_frequency = samp_freq
    print('The sample frequency is', lockin1.sample_frequency)

    t0 = time.time()
    res_bytes = lockin1.buffer_measure(count, fast=fast)
    # print('bytes meas time', time.time()-t0)
    
    print('The length of the result is', len(res_bytes))
    print('The buffer count is ', lockin1.buffer_count, 'script complete')
    print('time to complete', time.time()-t0)



if __name__ == '__main__':

    custom_method(
        fast = True,
        count = count_mult*samp_freq
        )
# The pymeasure method in the current version 

# reload(srs)
# lockin1 = srs.SR830('GPIB2::9::INSTR')
# print(lockin1.id)
# print(lockin1.x, lockin1.y)

# lockin1.sample_frequency = 128
# print(lockin1.sample_frequency)

# lockin1.reset_buffer()
# bin_t0 = time.time()
# res_bin = lockin1.buffer_measure(count = 256, delay=0); print(res_bin)
# print('meas time', time.time()-bin_t0)
# print(lockin1.buffer_count)



