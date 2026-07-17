# Sensor Profiles = a database (just data, no logic)
# Each profile describes HOW a sensor is identified (identifier_type),
# not just a generic "id", since detection differs by protocol.

SENSORS = {
    "ds18b20": {
        "name": "DS18B20",
        "type": "Temperature",
        "protocol": "1-Wire",
        "unit": "°C",
        "identifier_type": "family_code",
        "family_code": "28",          # shared by ALL DS18B20s
        "driver": "ds18b20_driver",
        "value_fields": ["temperature"],
    },

    "bme280": {
        "name": "BME280",
        "type": "Temperature/Humidity/Pressure",
        "protocol": "I2C",
        "unit": ["°C", "%", "hPa"],
        "identifier_type": "i2c_address",
        "i2c_address": "0x76",       # can also be 0x77 depending on wiring
        "driver": "bme280_driver",
        "value_fields": ["temperature", "humidity", "pressure"],
    },

    "dht11": {
        "name": "DHT11",
        "type": "Temperature/Humidity",
        "protocol": "GPIO-Timing",
        "unit": ["°C", "%"],
        "identifier_type": "manual_gpio",   # no auto-detect possible
        "identifier": None,
        "driver": "dht11_driver",
        "value_fields": ["temperature", "humidity"],
    }
}


def get_profile_by_family_code(family_code):
    for key, profile in SENSORS.items():
        if profile.get("identifier_type") == "family_code" and profile.get("family_code") == family_code:
            return key, profile
    return None, None


def get_profile_by_i2c_address(address):
    for key, profile in SENSORS.items():
        if profile.get("identifier_type") == "i2c_address" and profile.get("i2c_address") == address:
            return key, profile
    return None, None