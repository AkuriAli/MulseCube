from sensor_profile import SensorProfile
from sensor_measurement import SensorMeasurement
from drivers import digital_output_driver, mq8_esp32_driver


def _read(device_address):
    # device_address is the DO pin's GPIO number, same as before
    alarm = digital_output_driver.read_raw(device_address)
    ppm = mq8_esp32_driver.read_ppm()

    if alarm is None and ppm is None:
        return None

    result = {}
    if ppm is not None:
        result["hydrogen_ppm"] = ppm
    if alarm is not None:
        result["gas_alarm_triggered"] = alarm
    return result


PROFILE = SensorProfile(
    model="MQ-8",
    protocol="Analog (AO via ESP32) + Digital Threshold (DO)",
    is_analog=True,
    identifier_type="manual_gpio",
    identifier=None,
    read_fn=_read,
    measurements=[
        SensorMeasurement("hydrogen_ppm", "ppm", 100, 10000),
        SensorMeasurement("gas_alarm_triggered", "bool", 0, 1),
    ],
)