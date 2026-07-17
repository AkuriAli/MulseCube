# sensor_detector.py = the 'scanner' that figures out what's plugged in



from sensor_profiles import SENSORS, get_profile_by_family_code
from drivers import ds18b20_driver


def scan_all_sensors():
    """
    Sequentially checks each supported protocol for connected sensors.
    Returns a list of (sensor_key, profile, device_address) tuples.
    Currently implements 1-Wire (DS18B20). I2C/GPIO scanning to be added
    once BME280 / DHT11 wiring is in place and I have setup the Raspberry Pi to support those protocols.
    """
    found = []

    # --- 1-Wire scan ---
    for device_address in ds18b20_driver.scan_for_ds18b20():
        family_code = device_address.split("-")[0]
        sensor_key, profile = get_profile_by_family_code(family_code)
        if profile:
            found.append((sensor_key, profile, device_address))

    # --- I2C scan: TODO once BME280 is wired up ---



    # --- GPIO/manual scan: TODO once DHT11 config is decided --- and the other sensors are in ready

    return found

    # use logger and error handling to always know where your errors are coming from, and to avoid silent failures.,

    #you need to loop  end 
    # logic of sensor reading. Am i still getting data? check for potrs interval?
    # use QR codes for different analog sensors