import os
import random
import time

W1_BASE_DIR = "/sys/bus/w1/devices"


def _hardware_available():
    return os.path.isdir(W1_BASE_DIR)


def scan_for_ds18b20():
    """
    Returns a list of full device addresses (e.g. '28-000005e77dfa')
    found on the 1-Wire bus. Falls back to a fake device in dev mode.
    """
    if not _hardware_available():
        # Dev mode: pretend one DS18B20 is connected
        return ["28-mockdevice001"]

    devices = []
    for entry in os.listdir(W1_BASE_DIR):
        if entry.startswith("28-"):
            devices.append(entry)
    return devices


def read_raw(device_address):
    """
    Reads raw temperature from a specific DS18B20 by its full address.
    Returns temperature in °C, or None if the read failed.
    """
    if not _hardware_available():
        # Dev mode: simulate a plausible room-temperature reading
        return round(random.uniform(20.0, 26.0), 2)

    device_file = os.path.join(W1_BASE_DIR, device_address, "w1_slave")
    try:
        with open(device_file, "r") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return None

    # First line ends in "YES" if the checksum is valid
    if len(lines) < 2 or "YES" not in lines[0]:
        return None

    # Temperature is after "t=" in the second line, in millidegrees C
    equals_pos = lines[1].find("t=")
    if equals_pos == -1:
        return None

    temp_string = lines[1][equals_pos + 2:]
    return round(float(temp_string) / 1000.0, 2)