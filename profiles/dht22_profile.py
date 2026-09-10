from sensor_profile import SensorProfile
from sensor_measurement import SensorMeasurement
from drivers import dht_driver


def _read(device_address):
    temperature, humidity = dht_driver.read_raw(device_address)
    if temperature is None or humidity is None:
        return None
    return {"temperature": temperature, "humidity": humidity}


PROFILE = SensorProfile(
    model="DHT22",
    protocol="GPIO-Timing",
    is_analog=True,
    identifier_type="manual_gpio",
    identifier=None,
    read_fn=_read,
    resolve_address_fn=dht_driver.get_or_load_device,
    measurements=[
        SensorMeasurement("temperature", "Cel", -40, 80),
        SensorMeasurement("humidity", "%RH", 0, 100),
    ],
)