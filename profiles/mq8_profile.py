from sensor_profile import SensorProfile
from sensor_measurement import SensorMeasurement
from drivers import digital_output_driver


def _read(device_address):
    value = digital_output_driver.read_raw(device_address)
    if value is None:
        return None
    return {"hydrogen_gas_detected": value}


PROFILE = SensorProfile(
    model="MQ-8",
    protocol="Digital Threshold (DO)",
    is_analog=True,
    identifier_type="manual_gpio",
    identifier=None,
    read_fn=_read,
    # no resolve_address_fn needed - defaults to "just use the raw pin number"
    measurements=[
        SensorMeasurement("hydrogen_gas_detected", "bool", 0, 1),
    ],
)