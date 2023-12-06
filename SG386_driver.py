from pymeasure.instruments import Instrument
from pymeasure.instruments.validators import strict_discrete_set, truncated_range

class SG386(Instrument):

    def __init__(self, resourceName, **kwargs):
        super().__init__(
        resourceName,
        "Stanford Research Systems SG386 Signal Generator",
        **kwargs
    )    
    frequency = Instrument.control(
        get_command = "FREQ?", 
        set_command="FREQ%0.6f",
        docs = """ A floating point property that represents the output frequency
        in Hz. This property can be set. """,
        validator=truncated_range,
        values=[0, 6.075E9]
    )
    modulation_function = Instrument.control(
    get_command='MFNC?', 
    set_command='MFNC%d',
    docs='''A string property that represents the modulation function.
        This property can be set''',
    validator = strict_discrete_set,
    values = ['Sine wave', 'Ramp', 'Triangle', 'Square', 'Noise', 'External'],
    map_values=True
    )


    ###################################################################   SIGNAL SYNTHESIS COMMANDS   ################################################################### 


    amp_rms_bnc = Instrument.control(
        'AMPL?', 'AMPL%0.3f RMS',
        '''A floating point proeprty that represents the BNC output amplitude
        in Vrms.  This property can be set''',
        validator=truncated_range,
        values = [1E-3, 1]
    )

    offset_bnc = Instrument.control(
        'OFSL?', 'OFSL%0.3f', 
        '''Set (query) the offset voltage of the low frequency BNC output 
        in volts.''',
        validator= truncated_range,
        values = [-1.5, 1.5]
    )

    phase = Instrument.control(
        'PHASE?', 'PHASE%0.1f',   #PHASE RESOLUTION DEPENDS ON FREQUENCY
        '''A floating point property that represents the carrier's phase in degrees.  
        This property can be set''',
        validator = truncated_range,  # maybe use a modular valiator because phase is modulo 2pi
        values  = [-360, 360]  
    )

###################################################################   MOUDLATION COMMANDS   ###################################################################

    modulation_switch = Instrument.control(
        'MODL?', 'MODL%d',
        '''A string property that represents if modulation is on/off.
        This property can be set.''',
        validator= strict_discrete_set,
        values= ['off', 'on'],
        map_values= True
    )

    modulation_coupling = Instrument.control(
        'COUP?', 'COUP%d',
        '''A string property that represents if the external modulation input is
        AC/DC.  This property can be set''',
        validator = strict_discrete_set,
        values = ['AC', 'DC'],
        map_values=True
    )

    modulation_type = Instrument.control(
        'TYPE?', 'TYPE%d',
        '''A string property that represents the modulation type.
        This property can be set''',
        validator = strict_discrete_set,
        values = ['AM', 'FM', 'PM', 'Sweep', 'Pulse', 'Blank', 'IQ'],  ##TO DO IQ IS ONLY AVAILABLE IF OPTION 3 is installed.  Need to use scpi command to check if option 3 installed
        map_values=True
    )

    modulation_rate = Instrument.control(
        'RATE?', 'RATE%0.6f',
        '''A floating point property that represents the rate for AM/FM/PM.  
        This property also represents  the noise bandwidth for AM/FM/ΦM and IQ
        modulation if a noise function is selected for the given type of modulation.
        This property can be set''',
        validator = truncated_range,
        values = [1E-6, 500E3]
    )

    modulation_function = Instrument.control(
        'MFNC?', 'MFNC%d',
        '''A string property that represents the modulation function.
        This property can be set''',
        validator = strict_discrete_set,
        values = ['Sine wave', 'Ramp', 'Triangle', 'Square', 'Noise', 'External'],
        map_values=True
    )

    AM_depth = Instrument.control(
        'ADEP?', 'ADEP%0.1f',
        '''A floating point property that represents the AM depth percentage.
        This property can be set''',
        validator = truncated_range,
        values = [0, 100]
    )

    AM_noise_depth = Instrument.control(
        'ANDP?', 'ANDP%0.1f',
        '''A floating point property that represents the AM noise rms depth percentage.
        This property can be set''',
        validator = truncated_range,
        values = [0, 100]
    )

    FM_dev = Instrument.control(
        'FDEV?', 'FDEV%0.6f',
        '''A floating point property that represents the FM deviation in Hz''',
        validator = truncated_range,
        values = [0.1, 93.75E6] #not sure about this larger bound?, got it from the specifications
    )

    FM_noise_dev = Instrument.control(
        'FNDV?', 'FNDV%0.6f',
        '''A floating point property that represents the FM rms noise deviation in Hz.
        This property can be set. ''',
        validator = truncated_range,
        values = [0.1, 97.3E6]
    )

    PM_dev = Instrument.control(
        'PDEV?', 'PDEV%0.2f',
        '''A floating piont property that represents the PM deviation in degrees.
        This property can be set.''',
        validator = truncated_range, # maybe use a modular valiator because phase is modulo 2pi
        values = [0, 360]
    )

    PM_noise_dev = Instrument.control(
        'PNDV?', 'PNDV%0.2f',
        '''A floating point property represents the PM rms deviation in degrees.
        This property can be set.''',
        validator=truncated_range, # maybe use a modular valiator because phase is modulo 2pi
        values = [0, 360]
    )


###################################################################   ERROR CODE COMMANDS   ###################################################################