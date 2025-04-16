import numpy as np
from pymeasure.instruments.validators import (strict_discrete_set,
                                              discreteTruncate)
from pymeasure.instruments import Instrument


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
        docs='',
        validator=discreteTruncate,
        values=np.append(
            np.arange(1E-3, 99.99E-3+0.01E-3, 0.01E-3), 
            np.arange(100E-3, 1E3+1E-3, 1E-3)
        ).tolist()
    )

    measure_mode = Instrument.control(
        ':FUNC?', ':FUNC "%s"',
        '''Property for the measurement mode''',
        validator=strict_discrete_set,
        values=['FREQ', 'PER']
    )
