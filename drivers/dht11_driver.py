import os
import random

IIO_BASE_DIR = "/sys/bus/iio/devices"


def _hardware_available():
    return os.path.isdir(IIO_BASE_DIR)


def _find_dht11_device():
    """
    Searches all IIO devices for the one whose 'name' file says 'dht11'.
    Returns the full path to that device's folder, or None if not found.
    """
    if not _hardware_available():
        return None

    for entry in os.listdir(IIO_BASE_DIR):
        name_file = os.path.join(IIO_BASE_DIR, entry, "name")
        if os.path.exists(name_file):
            with open(name_file, "r") as f:
                if "dht11" in f.read().strip().lower():
                    return os.path.join(IIO_BASE_DIR, entry)

    return None


def read_raw():
    """
    Reads temperature (C) and humidity (%) from the DHT11 via the kernel's
    IIO interface. Falls back to mock values if no real device is found
    (e.g. running on a laptop, or overlay not enabled yet).
    Returns (temperature, humidity), or (None, None) on a read failure.
    """
    device_path = _find_dht11_device()

    if device_path is None:
        return None, None  # No real device found, caller can handle this (e.g. retry or use mock)

    try:
        with open(os.path.join(device_path, "in_temp_input"), "r") as f:
            temp_raw = int(f.read().strip())
        with open(os.path.join(device_path, "in_humidityrelative_input"), "r") as f:
            humidity_raw = int(f.read().strip())
    except (FileNotFoundError, ValueError):
        # DHT11 reads fail fairly often (timing-sensitive protocol) -
        # this is expected occasionally, calling code should just retry
        return None, None

    # IIO reports these in milli-units (e.g. 23500 = 23.5 C)
    temperature = temp_raw / 1000.0
    humidity = humidity_raw / 1000.0

    return temperature, humidity