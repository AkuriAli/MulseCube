from sensor_profile import SensorProfile
from sensor_measurement import SensorMeasurement
from drivers import digital_output_driver, mq8_esp32_driver


def _read(device_address):
    # device_address is {"adc_channel": n} when the MQ-8 sits in a numbered port (analog only),
    # or the GPIO number of its DO wire, or None if there is no DO wire (default channel).
    if isinstance(device_address, dict):
        alarm, channel = None, device_address.get("adc_channel")
    else:
        alarm = digital_output_driver.read_raw(device_address) if device_address is not None else None
        channel = None
    ppm = mq8_esp32_driver.read_ppm(channel)
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
    release_fn=digital_output_driver.release,
    works_without_pin=True,
    measurements=[
        SensorMeasurement("hydrogen_ppm", "ppm", 100, 10000),
        SensorMeasurement("gas_alarm_triggered", "bool", 0, 1),
    ],
)
