import serial
import json
import time

SERIAL_PORT = "/dev/ttyUSB0"   # confirm actual path - run 'ls /dev/ttyUSB* /dev/ttyACM*' after plugging in
BAUD_RATE = 115200

_serial_conn = None
_latest_readings = {}


def _ensure_connection():
    global _serial_conn
    if _serial_conn is None:
        _serial_conn = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
        time.sleep(2)   # plugging in often resets the ESP32 - give it time to reboot and start sending


def _drain_and_get_latest_line():
    """
    Reads every line currently sitting in the serial buffer and keeps only
    the most recent one, so a brief delay in our polling never means we
    act on a stale, backlogged reading.
    """
    _ensure_connection()
    latest_line = None
    while _serial_conn.in_waiting:
        line = _serial_conn.readline().decode(errors="ignore").strip()
        if line:
            latest_line = line
    return latest_line


def read_channel(channel_index):
    """
    Returns the voltage for one ESP32 analog channel (0, 1, or 2), or None
    if no valid reading is currently available.
    """
    global _latest_readings

    line = _drain_and_get_latest_line()
    if line:
        try:
            _latest_readings = json.loads(line)
        except json.JSONDecodeError:
            pass   # keep the previous good reading if this line was garbled

    return _latest_readings.get(f"ch{channel_index}")