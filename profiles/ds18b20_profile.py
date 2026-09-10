from sensor_profile import SensorProfile
from sensor_measurement import SensorMeasurement
from drivers import ds18b20_driver


def _read(device_address):
    temp = ds18b20_driver.read_raw(device_address)
    if temp is None:
        return None
    return {"temperature": temp}


PROFILE = SensorProfile(
    model="DS18B20",
    protocol="1-Wire",
    is_analog=False,
    identifier_type="family_code",
    identifier="28",
    read_fn=_read,
    measurements=[
        SensorMeasurement("temperature", "Cel", -55, 125),
    ],
)