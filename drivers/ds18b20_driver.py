import os

W1_BASE_DIR = "/sys/bus/w1/devices"

_last_problem = {}      # device address -> last problem printed, so each is reported once


def _note_problem(address, message):
    """Report a problem once per device; report again if it recovers and fails later."""
    if message is None:
        _last_problem.pop(address, None)
    elif _last_problem.get(address) != message:
        _last_problem[address] = message
        print(f"  DS18B20 {address}: {message}")


def _hardware_available():
    return os.path.isdir(W1_BASE_DIR)


def scan_for_ds18b20():
    """
    Returns a list of full device addresses (e.g. '28-000005e77dfa')
    found on the 1-Wire bus. Returns an empty list if 1-Wire isn't enabled
    or nothing is connected.
    """
    if not _hardware_available():
        return []

    devices = []
    for entry in os.listdir(W1_BASE_DIR):
        if entry.startswith("28-"):
            devices.append(entry)
    return devices


def read_raw(device_address):
    """
    Reads the temperature in degrees C from one DS18B20, by its full address.
    Returns None if it can't be read, and says why (once).
    """
    if not _hardware_available():
        return None

    device_file = os.path.join(W1_BASE_DIR, device_address, "w1_slave")
    try:
        with open(device_file, "r") as f:
            lines = f.readlines()
    except OSError as e:
        # covers a missing file and the I/O errors raised when a sensor is unplugged mid-read
        _note_problem(device_address, f"can't read it: {e}")
        return None

    if len(lines) < 2 or "YES" not in lines[0]:
        # The sensor's own checksum failed. Almost always a wiring problem:
        # a missing 4.7k pull-up between DATA and 3.3V, or a loose wire.
        _note_problem(device_address, "checksum failed - check the 4.7k pull-up resistor "
                                      "between DATA and 3.3V, and the wiring")
        return None

    equals_pos = lines[1].find("t=")
    if equals_pos == -1:
        _note_problem(device_address, "reply had no temperature in it")
        return None

    try:
        value = round(float(lines[1][equals_pos + 2:]) / 1000.0, 2)
    except ValueError:
        _note_problem(device_address, "reply had an unreadable temperature")
        return None

    _note_problem(device_address, None)
    return value
