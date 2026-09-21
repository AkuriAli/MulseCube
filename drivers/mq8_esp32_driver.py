import time
from drivers import esp32_serial_driver

MQ8_CHANNEL = 0   # which ESP32 analog channel the MQ-8's AO pin is wired to

SUPPLY_VOLTAGE = 3.3   # matches the ESP32's ADC reference voltage
RL = 10000             # load resistor on the MQ-8 module - check your specific board

# --- Calibration values: MUST be determined for YOUR specific sensor unit ---
# See calibrate_ro() below. Do not substitute a value copied from the internet.
RO_CLEAN_AIR = None
CURVE_M = None
CURVE_B = None


def _calculate_rs(voltage):
    if voltage is None or voltage <= 0:
        return None
    return (SUPPLY_VOLTAGE - voltage) / voltage * RL


def calibrate_ro(known_clean_air_ratio, samples=50):
    """
    Run this ONCE, sensor warmed up, sitting in known clean air, to find
    RO_CLEAN_AIR for your specific unit.
    """
    readings = []
    for _ in range(samples):
        voltage = esp32_serial_driver.read_channel(MQ8_CHANNEL)
        rs = _calculate_rs(voltage)
        if rs:
            readings.append(rs)
        time.sleep(0.1)

    if not readings:
        return None

    average_rs = sum(readings) / len(readings)
    return average_rs / known_clean_air_ratio


def read_ppm():
    """
    Returns hydrogen concentration in ppm, or None if not yet calibrated,
    the ESP32 isn't sending data, or the read failed.
    """
    if RO_CLEAN_AIR is None or CURVE_M is None or CURVE_B is None:
        return None

    voltage = esp32_serial_driver.read_channel(MQ8_CHANNEL)
    rs = _calculate_rs(voltage)
    if rs is None:
        return None

    ratio = rs / RO_CLEAN_AIR
    if ratio <= 0:
        return None

    ppm = (ratio / (10 ** CURVE_B)) ** (1 / CURVE_M)
    return round(ppm, 1)