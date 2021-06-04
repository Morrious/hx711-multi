
#!/usr/bin/env python3
"""
This file holds HX711 class and LoadCell class which is used within HX711 in order to track multiple load cells
"""

from time import sleep, perf_counter
from hx711.utils import convert_to_list

SIMULATE_PI = False
try:
    import RPi.GPIO as GPIO
except: 
    # set to simulate mode if unable to import GPIO (non raspberry pi run)
    SIMULATE_PI = True

class HX711:
    """
    HX711 class holds data for one or multiple load cells. All load cells must be using the same clock signal and be the same channel and gain setting
    
    Args:
        dout_pin (int or [int]): Raspberry Pi GPIO pins where data from HX711 is received
        sck_pin (int): Raspberry Pi clock output pin where sck signal to HX711 is sent
        gain_channel_A (int): Optional, by default value 128. Options (128 || 64)
        select_channel (str): Optional, by default 'A'. Options ('A' || 'B')
        debug_mode (bool): Optional, False by default

    Raises:
        TypeError:
            if dout_pins not an int or list of ints
            if gain_channel_A or select_channel not match required values
    """
    
    def __init__(self,
                 dout_pins,
                 sck_pin: int,
                 channel_A_gain: int = 128,
                 channel_select: str = 'A',
                 debug_mode: bool = False,
                 ):
        
        self._debug_mode = debug_mode
        self._set_dout_pins(dout_pins)
        self._set_sck_pin(sck_pin)
        # init GPIO before channel because a read operation is required for channel initialization
        self._init_gpio()
        self._set_channel_a_gain(channel_A_gain)
        self._set_channel_select(channel_select)
        self._init_load_cells()
                
    def _set_dout_pins(self, dout_pins):
        # set dout_pins as array of ints. If just an int input, turn it into a single array of int
        self._dout_pins = convert_to_list(dout_pins, _type=int, _default_output=None)
        if self._dout_pins is None:
            # raise error if pins not set properly
            raise TypeError(f'dout_pins must be type int or array of int.\nReceived dout_pins: {dout_pins}')
        
    def _set_sck_pin(self, sck_pin):
        # set sck_pin if int
        if type(sck_pin) is not int:
            raise TypeError(f'sck_pin must be type int.\nReceived sck_pin: {sck_pin}')
        self._sck_pin = sck_pin
                    
    def _init_gpio(self):
        # init GPIO
        if not SIMULATE_PI:
            GPIO.setup(self._sck_pin, GPIO.OUT)  # sck_pin is output only
            for dout in self._dout_pins:
                GPIO.setup(dout, GPIO.IN)  # dout_pin is input only
            
    def _set_channel_a_gain(self, channel_A_gain):
        # check channel_select for type and value. Default is A if None
        if channel_A_gain not in [128, 64]:
            # raise error if channel not 128 or 64
            raise TypeError(f'channel_A_gain must be A or B.\nReceived channel_A_gain: {channel_A_gain}')
        self._channel_A_gain = channel_A_gain
        
    def _set_channel_select(self, channel_select):
        # check channel_select for type and value. Default is A if None
        if channel_select not in ['A', 'B']:
            # raise error if channel not A or B
            raise TypeError(f'channel_select must be A or B.\nReceived channel_select: {channel_select}')
        self._channel_select = channel_select
                
    def _init_load_cells(self):
        # initialize load cell instances
        self._load_cells = []
        for dout_pin in self._dout_pins:
            self._load_cells.append(LoadCell(dout_pin, self._debug_mode))

    def _prepare_to_read(self):
        """
        prepare to read by setting SCK output to LOW and loop until all dout inputs are LOW

        Returns:
            bool : True if ready to read else False 
        """
        
        GPIO.output(self._pd_sck, False)  # start by setting the pd_sck to 0
        
        # check if ready a maximum of 20 times (~200ms)
        ready = True
        for _ in range(20):
            # confirm all dout pins are ready (LOW)
            ready = True
            for _load_cell in self._load_cells:
                if GPIO.input(_load_cell._dout_pin) == 0:
                    ready = False
            if ready:
                break
            else:
                # if not ready sleep for 10ms before next iteration
                sleep(0.01)                
        return ready
    
    def _pulse_sck_high(self):
        """
        Pulse SCK pin high shortly
        
        Returns:
            bool: True if pulse was shorter than 60 ms
        """
        
        pulse_start = perf_counter()
        GPIO.output(self._pd_sck, True)
        GPIO.output(self._pd_sck, False)
        pulse_end = perf_counter()
        # check if pulse lasted 60ms or longer. If so, HX711 enters power down mode
        if pulse_end - pulse_start >= 0.00006:  # check if the hx 711 did not turn off...
            # if pd_sck pin is HIGH for 60 us and more than the HX 711 enters power down mode.
            if self._debug_mode:
                print(f'sck pulse lasted for longer than 60ms\nTime elapsed: {pulse_end - pulse_start}')
            return False
        return True
    
    def _write_channel_gain(self):
        """
        _write_channel_gain must be run after each 24-bit read
        pulses SCK pin 1, 2, or 3 times based on channel configuration
        
        A, 128 : total pulses = 25 (24 read data, 1 extra to set dout back to high)
        A, 64 : total pulses = 27 (24 read data, 3 extra to set dout back to high)
        B, 32 : total pulses = 26 (24 read data, 2 extra to set dout back to high)
        
        Returns:
            bool: True if pulsees were all successful
        """
        
        # get number of pulses based on channel configuration
        num_pulses = 2 # default 2 for channel B
        if self._channel_select == 'A' and self._channel_A_gain == 128:
            num_pulses = 1
        elif self._channel_select == 'A' and self._channel_A_gain == 64:
            num_pulses = 3
        
        # pulse num_pulses
        for _ in range(num_pulses):
            if not self._pulse_sck_high():
                return False
        return True
            
        
        
    def _read(self):
        """
        read each bit from HX711, convert to signed int, and validate
        operation:
            1) set SCK output HIGH, loop until all dout pins are LOW (_prepare_to_read)
            2) read first 24 bits of each LoadCell by pulsing SCK output for each bit
            3) set channel gain following read by pulsing SCK to result in a total of 25, 26, or 27 SCK pulses for a read operation (see documentation)
        
        Returns:
            bool : returns True if successful. Readings are assigned to LoadCell objects
        """
        
        # prepare for read by setting SCK pin and checking that each load cell is ready
        if not self._prepare_to_read():
            if self._debug_mode:
                print('_prepare_to_read() not ready after 20 iterations\n')
            return False
        
        # read first 24 bits of data (the raw data bits)
        load_cell: LoadCell
        for load_cell in self._load_cells:
            load_cell._init_raw_read()
        for _ in range(24):
            # pulse sck high to request each bit
            if not self._pulse_sck_high():
                return False
            for load_cell in self._load_cells:
                load_cell._read()
        for load_cell in self._load_cells:
            load_cell._finish_raw_read()
                
        # set channel after read
        if not self._write_channel_gain():
            return False
        
        return True
            
class LoadCell:
    """
    LoadCell class holds data for one load cell
    """
    
    def __init__(self,
                 dout_pin,
                 debug_mode,
                 ):
        self._dout_pin = dout_pin
        self._debug_mode = debug_mode
        self._offset = 0.
        self._scale_ratio = 1.
        self._last_raw_read = None
        self.raw_reads = []
        self.reads = []
        
    def _init_raw_read(self):
        # set raw read value to zero, so each bit can be shifted into this value
        self._current_raw_read = 0
    
    def _read(self):
        # left shift by one bit then bitwise OR with the new bit
        self._current_raw_read = (self._current_raw_read << 1) | GPIO.input(self._dout_pin)
        
    def _finish_raw_read(self):
        # append current raw read value to raw_reads list
        self.raw_reads.append(self._current_raw_read)
        # convert to signed value
        self._current_signed_value = self.convert_to_signed_value(self._current_raw_read)
        self.reads.append(self._current_signed_value)
        if self._debug_mode:
            # print 2's complement value and signed value
            print(f'Binary value as received: {bin(self._current_raw_read)}\nSigned value: {self._current_signed_value}')
            
    def convert_to_signed_value(self, raw_value):
        # convert to signed value after verifying value is valid
        #check if data is valid by checking betwwen valid range: 0x800000 - 0x7fffff
        if not (0x7fffff < raw_value < 0x800000):
            if self._debug_mode:
                print('Invalid raw value detected: {}\n'.format(raw_value))
            return None  # return None because the data is invalid
        # calculate int from 2's complement
        # check if the sign bit is 1, indicating a negative number
        if (raw_value & 0x800000):
            signed_value = -((raw_value ^ 0xffffff) + 1)  # convert from 2's complement to negative int
        else:  # else do not do anything the value is positive number
            signed_value = raw_value
        return signed_value
    
    def _init_set_of_reads(self):
        self.raw_reads = []
        self.reads = []
        