import re
import time
import numpy as np
from enum import IntFlag
from pymeasure.instruments import Instrument, discreteTruncate
from pymeasure.instruments.validators import strict_discrete_set, \
    truncated_discrete_set, truncated_range

from pymeasure.instruments import Instrument
from pymeasure.instruments.validators import strict_discrete_set, truncated_range


###################################################################   ERROR CODE COMMANDS   ###################################################################
class DS345(Instrument):

    def __init__(self, resourceName, **kwargs):
        super().__init__(
            resourceName,
            "Stanford Research Systems DS345 30MHz Signal Generator",
            **kwargs
        )

    frequency = Instrument.control(
        get_command = "FREQ?", 
        set_command="FREQ%0.6f",
        docs = """ A floating point property that represents the output frequency
        in Hz. This property can be set. """,
        validator=truncated_range,
        values=[0, 30.2E6]
    )

    amp_rms = Instrument.control(
        'AMPL?', 'AMPL%0.3f VR',
        '''A floating point proeprty that represents the BNC output amplitude
        in Vrms.  This property can be set''',
        validator=truncated_range,
        values = [3.54E-3, 3.54]
    )

    amp_pp = Instrument.control(
        'AMPL?', 'AMPL%0.3f VP',
        '''A floating point proeprty that represents the BNC output amplitude
        in VPP.  This property can be set''',
        validator=truncated_range,
        values = [10E-3, 10]
    )

def main():
    # rm = pyvisa.ResourceManager()
    # gpib_list = rm.list_resources()
    # print(pymeasure.__version__)
    fgen = DS345('GPIB2::16::INSTR')
    print(fgen.id)
    print(fgen.amp_rms)
    print(fgen.frequency)

if __name__ == '__main__':
    main()