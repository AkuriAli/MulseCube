from sensor_profile import SensorProfile
from sensor_measurement import SensorMeasurement
from drivers import dht20_driver


def _read(device_address):
    temperature, humidity = dht20_driver.read_raw(device_address)
    if temperature is None or humidity is None:
        return None
    return {"temperature": temperature, "humidity": humidity}


PROFILE = SensorProfile(
    model="DHT20",
    protocol="I2C",
    is_analog=False,
    identifier_type="i2c_address",
    identifier=0x38,
    read_fn=_read,
    measurements=[
        SensorMeasurement("temperature", "Cel", -40, 80),
        SensorMeasurement("humidity", "%RH", 0, 100),
    ],
)