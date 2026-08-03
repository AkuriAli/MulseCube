import time

from sensor_profiles import SENSORS
from drivers import ds18b20_driver, dht11_driver
from standardizer import standardize


def display_menu():
    """Prints the list of known sensor profiles for the user to choose from."""
    print("\nSensor detected on port. Please confirm which sensor this is:")
    print("-------------------------------------------------------------")
    for i, (key, profile) in enumerate(SENSORS.items(), start=1):
        print(f"{i}. {profile['name']}  ({profile['protocol']})")
    print("0. Exit")


def get_user_selection():
    """Loops until the user enters a valid menu choice. Returns the sensor key, or None to exit."""
    keys = list(SENSORS.keys())

    while True:
        display_menu()
        choice = input("\nEnter number: ").strip()

        if choice == "0":
            return None

        if choice.isdigit() and 1 <= int(choice) <= len(keys):
            return keys[int(choice) - 1]

        print("Invalid selection, please try again.")


def run_ds18b20_loop():
    """
    Reads DS18B20 values on a loop and prints standardized readings.
    Uses the real 1-Wire bus if available (e.g. on the Pi with a sensor wired up),
    otherwise falls back to mock values automatically (see ds18b20_driver.py).
    """
    device_list = ds18b20_driver.scan_for_ds18b20()

    if not device_list:
        print("\nNo DS18B20 device found on the 1-Wire bus. Check wiring and try again.")
        return

    device_address = device_list[0]
    profile = SENSORS["ds18b20"]

    print(f"\nReading from: {device_address}")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            raw_temp = ds18b20_driver.read_raw(device_address)

            if raw_temp is None:
                print("Read failed (bad checksum or disconnected).")
            else:
                record = standardize("ds18b20", profile, "temperature", raw_temp)
                print(f"Reading: {record}")

            time.sleep(2)

    except KeyboardInterrupt:
        print("\nStopped by user.")


def run_dht11_loop():
    """
    Reads DHT11 values on a loop and prints standardized readings for
    both temperature and humidity. Uses the real kernel IIO device if the
    dtoverlay is enabled and a sensor is wired up.
    """
    profile = SENSORS["dht11"]

    print(f"\nExpected wiring: DHT11 data pin -> GPIO{profile['identifier']}")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            temperature, humidity = dht11_driver.read_raw()

            if temperature is None or humidity is None:
                print("Values not available (sensor not detected or read failed).")
            else:
                temp_record = standardize("dht11", profile, "temperature", temperature)
                humidity_record = standardize("dht11", profile, "humidity", humidity)
                print(f"Reading: {temp_record}")
                print(f"Reading: {humidity_record}")

            time.sleep(2)

    except KeyboardInterrupt:
        print("\nStopped by user.")


def main():
    sensor_key = get_user_selection()

    if sensor_key is None:
        print("Exiting.")
        return

    profile = SENSORS[sensor_key]
    print(f"\nSelected: {profile['name']} ({profile['protocol']})")

    if sensor_key == "ds18b20":
        run_ds18b20_loop()
    elif sensor_key == "dht11":
        run_dht11_loop()
    else:
        print(f"\nNo driver implemented yet for {profile['name']}. Coming soon.")


if __name__ == "__main__":
    main()