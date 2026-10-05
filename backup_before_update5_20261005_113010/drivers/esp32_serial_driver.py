import json
import time

import serial

SERIAL_PORT = "/dev/ttyUSB0"   # confirm actual path - run 'ls /dev/ttyUSB* /dev/ttyACM*' after plugging in
BAUD_RATE = 115200

STARTUP_WAIT = 2     # plugging in resets the ESP32 - give it time to reboot and start sending
MAX_BUFFER = 4096    # bytes kept while looking for the newest reading (bounds memory)
MAX_AGE = 5          # seconds - a reading older than this is not passed on as current

_serial_conn = None
_buffer = b""
_latest_readings = {}
_last_good = None        # when the last valid reading arrived
_opened_at = None
_brownout_seen = False
_last_message = None


def _say_once(message):
    """Print a problem once, not every cycle. Reset when things recover."""
    global _last_message
    if message is None:
        _last_message = None            # recovered: stay quiet, but report a repeat next time
    elif message != _last_message:
        _last_message = message
        print(f"  ESP32: {message}")


def _open():
    global _serial_conn, _buffer, _last_good, _opened_at, _brownout_seen
    _serial_conn = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
    _buffer = b""
    _last_good = None
    _brownout_seen = False
    _opened_at = time.time()
    time.sleep(STARTUP_WAIT)


def _close():
    global _serial_conn
    try:
        if _serial_conn is not None:
            _serial_conn.close()
    except Exception:
        pass
    _serial_conn = None


def _absorb_new_data():
    """
    Takes whatever has arrived and keeps the newest valid reading. Never waits
    and never loops on incoming data, so a chatty or broken ESP32 (for example
    one stuck rebooting and printing boot messages) cannot hang the caller.
    """
    global _buffer, _latest_readings, _last_good, _brownout_seen

    waiting = _serial_conn.in_waiting
    if waiting:
        _buffer += _serial_conn.read(min(waiting, MAX_BUFFER))
    if len(_buffer) > MAX_BUFFER:
        _buffer = _buffer[-MAX_BUFFER:]          # keep only the newest data

    lines = _buffer.split(b"\n")
    _buffer = lines.pop()                        # text after the last newline is unfinished

    newest = None
    for raw in lines:
        text = raw.decode(errors="ignore").strip()
        if "brownout" in text.lower():
            _brownout_seen = True
        if text.startswith("{"):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                newest = parsed

    if newest is not None:
        _latest_readings = newest
        _last_good = time.time()


def read_channel(channel_index):
    """
    Returns the voltage for one ESP32 analog channel (0, 1 or 2), or None if
    there is no current reading - the port can't be opened, nothing valid has
    arrived, or the ESP32 stopped sending. Says why (once) when that happens.
    """
    try:
        if _serial_conn is None:
            _open()
        _absorb_new_data()
    except (serial.SerialException, OSError) as e:
        _close()
        _say_once(f"can't talk to {SERIAL_PORT}: {e}")
        return None

    now = time.time()
    brownout_message = ("it keeps resetting with a brownout - its power supply is too weak. "
                        "Try another USB port or cable, or a powered USB hub.")

    if _last_good is None:
        if _brownout_seen:
            _say_once(brownout_message)
        elif now - _opened_at > 3:
            _say_once("connected, but no readings have arrived - is the sensor sketch flashed on it?")
        return None

    if now - _last_good > MAX_AGE:
        _say_once(brownout_message if _brownout_seen
                  else "it stopped sending readings (unplugged, or resetting)")
        return None

    _say_once(None)          # healthy: forget the last problem so a repeat is reported again
    return _latest_readings.get(f"ch{channel_index}")
