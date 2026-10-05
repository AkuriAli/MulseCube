import os

W1_BASE_DIR = "/sys/bus/w1/devices"


def _hardware_available():
    return os.path.isdir(W1_BASE_DIR)


def scan_for_ds18b20():
    """
    Returns a list of full device addresses (e.g. '28-000005e77dfa')
    found on the 1-Wire bus. Returns an empty list if no real hardware
    is present (no mock fallback anymore).
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
    Reads raw temperature from a specific DS18B20 by its full address.
    Returns temperature in °C, or None if the read failed or hardware
    isn't present.
    """
    if not _hardware_available():
        return None

    device_file = os.path.join(W1_BASE_DIR, device_address, "w1_slave")
    try:
        with open(device_file, "r") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return None

    if len(lines) < 2 or "YES" not in lines[0]:
        return None

    equals_pos = lines[1].find("t=")
    if equals_pos == -1:
        return None

    temp_string = lines[1][equals_pos + 2:]
    return round(float(temp_string) / 1000.0, 2)