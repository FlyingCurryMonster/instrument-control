# sr830_bufferA.pyx
cpdef int square(int x):
    return x*x

# sr830_interface.pyx
cdef extern from "pyvisa.resources import ResourceManager":
    cdef object ResourceManager
    ResourceManager = object

cdef extern from "pyvisa.resources import MessageBasedResource":
    cdef object MessageBasedResource
    MessageBasedResource = object

cdef class SR830Interface:
    cdef MessageBasedResource instrument

    def __cinit__(self, str address):
        cdef ResourceManager rm = ResourceManager()
        self.instrument = rm.open_resource(address)  # Replace with your instrument address

    def read_buffer(self) -> float:
        cdef bytes x_buffer = self.instrument.query_raw('T')
        return self.instrument.query_raw("TRCA? TRACE1")  # Query buffer for Trace 1 data


    def pause_buffer(self) -> None:
        self.instrument.write("PAUS")

    def start_buffer(self) -> None:
        self.instrument.write("STRT")

    def reset_buffer(self) -> None:
        self.instrument.write("REST")
