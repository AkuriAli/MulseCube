
# main.py = the conductor that calls everything else in order


from sensor_detector import scan_all_sensors
from drivers import ds18b20_driver
from standardizer import standardize

print("Scanning for connected sensors...\n")

detected = scan_all_sensors()

if not detected:
    print("No sensors detected.")
else:
    for sensor_key, profile, device_address in detected:
        print(f"Sensor Detected: {profile['name']} ({device_address})")
        print(f"  Protocol: {profile['protocol']}")

        if profile["driver"] == "ds18b20_driver":
            raw_temp = ds18b20_driver.read_raw(device_address)
            if raw_temp is None:
                print("  Read failed (bad checksum or disconnected).\n")
                continue

            record = standardize(sensor_key, profile, "temperature", raw_temp)
            print(f"  Standardized reading: {record}\n")