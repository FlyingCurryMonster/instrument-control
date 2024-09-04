06/05/2024 01:35:05 AM : Worker caught an error on (ERROR)
  Traceback (most recent call last):
    File "C:\Users\Jeevak\anaconda3\lib\site-packages\pymeasure\experiment\workers.py", line 176, in run
      self.procedure.execute()
    File "C:\Users\Jeevak\Documents\GitHub\instrument-control\fsweep_tek_dual_sr830buffer.py", line 84, in execute
      x2_buff, y2_buff = meas_2.result()
    File "C:\Users\Jeevak\anaconda3\lib\concurrent\futures\_base.py", line 438, in result
      return self.__get_result()
    File "C:\Users\Jeevak\anaconda3\lib\concurrent\futures\_base.py", line 390, in __get_result
      raise self._exception
    File "C:\Users\Jeevak\anaconda3\lib\concurrent\futures\thread.py", line 52, in run
      result = self.fn(*self.args, **self.kwargs)
    File "C:\Users\Jeevak\anaconda3\lib\site-packages\pymeasure\instruments\srs\sr830.py", line 561, in buffer_measure_from_bytes
      self.wait_for_buffer(count)
    File "C:\Users\Jeevak\anaconda3\lib\site-packages\pymeasure\instruments\srs\sr830.py", line 540, in wait_for_buffer
      while not self.buffer_count >= count and i     File "C:\Users\Jeevak\anaconda3\lib\site-packages\pymeasure\instruments\common_base.py", line 298, in __getattribute__
      return super().__getattribute__(name)
    File "C:\Users\Jeevak\anaconda3\lib\site-packages\pymeasure\instruments\srs\sr830.py", line 483, in buffer_count
      return int(query)
  ValueError: invalid literal for int() with base 10: '\n'
06/05/2024 01:35:05 AM : Monitor caught stop command (INFO)