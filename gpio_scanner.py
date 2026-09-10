import time
from gpiozero import InputDevice, Device

# Pins already claimed by dedicated protocols - never scan these generically,
# since I2C/1-Wire auto-detection already covers them.
RESERVED_PINS = {2, 3, 4, 14, 15}   # I2C (2,3), 1-Wire (4), UART (14,15)

# Common "free" GPIOs on a standard 40-pin header, excluding reserved ones.
CANDIDATE_PINS = [5, 6, 12, 13, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27]

SETTLE_DELAY = 0.05
SAMPLES_PER_TEST = 3
SAMPLE_DELAY = 0.01


def _stable_read(pin):
    """
    Takes several readings under the internal pull-down. Returns the value
    only if every sample agrees (filters out electrical noise); returns
    None if readings were inconsistent.
    """
    device = InputDevice(pin, pull_up=False)
    time.sleep(SETTLE_DELAY)   # let the line settle before trusting any reading

    readings = []
    for _ in range(SAMPLES_PER_TEST):
        readings.append(device.value)
        time.sleep(SAMPLE_DELAY)
    device.close()

    if all(r == readings[0] for r in readings):
        return readings[0]
    return None


def scan_gpio_connections():
    """
    Checks each candidate pin with the internal pull-down enabled. A pin
    reading HIGH is treated as connected. (The earlier dual pull-up/pull-down
    test was dropped - the pull-up half proved unreliable on this Pi 5's GPIO
    stack, while this single pull-down test matches confirmed real hardware.
    Note: sensors that idle LOW instead of HIGH would still be missed by this
    test - the manual "add sensor" fallback in main.py exists for that case.)
    """
    connected = []

    for pin in CANDIDATE_PINS:
        try:
            reading = _stable_read(pin)
            if reading:
                connected.append(pin)
        except Exception:
            continue

    if Device.pin_factory is not None:
        Device.pin_factory.close()
        Device.pin_factory = None

    return connected