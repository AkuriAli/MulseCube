import subprocess
import os
import time

IIO_BASE_DIR = "/sys/bus/iio/devices"

_pin_device_cache = {}   # gpio_pin -> resolved sysfs device path (avoids reloading the overlay every read)


def _current_iio_devices():
    if not os.path.isdir(IIO_BASE_DIR):
        return set()
    return set(os.listdir(IIO_BASE_DIR))


def get_or_load_device(gpio_pin):
    """
    Loads a dht11-family overlay for this exact GPIO pin at runtime (works for
    both DHT11 and DHT22 - same underlying kernel driver), and returns the
    sysfs path to the device that appears as a result. This ties the device
    unambiguously to the pin the user just confirmed, no ordering guesswork.
    Requires the script to be run with sudo.
    """
    if gpio_pin in _pin_device_cache:
        return _pin_device_cache[gpio_pin]

    before = _current_iio_devices()

    try:
        subprocess.run(
            ["dtoverlay", "dht11", f"gpiopin={gpio_pin}"],
            check=True, capture_output=True
        )
    except subprocess.CalledProcessError as e:
        print(f"  Failed to load overlay for GPIO{gpio_pin}: {e}")
        return None

    time.sleep(0.5)  # brief pause for the new device to register
    new_devices = _current_iio_devices() - before

    if not new_devices:
        print(f"  Overlay loaded but no new device appeared for GPIO{gpio_pin}.")
        return None

    device_path = os.path.join(IIO_BASE_DIR, new_devices.pop())
    _pin_device_cache[gpio_pin] = device_path
    return device_path


def read_raw(device_path):
    """
    Reads temperature (C) and humidity (%) from an already-resolved device path.
    Returns (temperature, humidity), or (None, None) on failure.
    """
    if device_path is None:
        return None, None

    try:
        with open(os.path.join(device_path, "in_temp_input"), "r") as f:
            temp_raw = int(f.read().strip())
        with open(os.path.join(device_path, "in_humidityrelative_input"), "r") as f:
            humidity_raw = int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return None, None

    return temp_raw / 1000.0, humidity_raw / 1000.0