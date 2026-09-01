from sensor import Sensor
from profile_registry import find_by_family_code, get_all_profiles
from drivers import ds18b20_driver


def auto_detect_sensors():
    """
    Scans every protocol capable of self-identification and builds a Sensor
    object (with its profile already attached) for each match found.
    Returns a list of identified Sensor objects - empty if nothing was found.
    """
    identified = []

    # --- 1-Wire scan (DS18B20, and any future 1-Wire sensors) ---
    for device_address in ds18b20_driver.scan_for_ds18b20():
        family_code = device_address.split("-")[0]
        profile = find_by_family_code(family_code)

        if profile:
            sensor = Sensor.digital(
                name=profile.model,
                family=profile.model,
                familycode=family_code,
                protocol=profile.protocol,
                device_address=device_address,
            )
            sensor.apply_profile(profile)
            identified.append(sensor)

    # --- I2C scan: TODO once BME280 is wired up (needs smbus2) ---

    return identified


def manual_fallback_selection():
    """
    Used when auto-detection finds nothing - lets the user confirm what's
    connected, the same menu flow you already had. This is the only route
    for sensors that can NEVER self-identify (e.g. DHT11's GPIO-timing
    protocol has no address to scan for).
    """
    profiles = get_all_profiles()

    print("\nCould not auto-identify a connected sensor.")
    print("Please confirm which sensor this is:")
    print("---------------------------------------")
    for i, profile in enumerate(profiles, start=1):
        print(f"{i}. {profile.model}  ({profile.protocol})")
    print("0. Skip / exit")

    while True:
        choice = input("\nEnter number: ").strip()

        if choice == "0":
            return None

        if choice.isdigit() and 1 <= int(choice) <= len(profiles):
            selected_profile = profiles[int(choice) - 1]

            if selected_profile.is_analog:
                sensor = Sensor.analog(
                    name=selected_profile.model, gnd=None, vcc=None, pincount=1
                )
            else:
                sensor = Sensor.digital(
                    name=selected_profile.model, family=selected_profile.model,
                    familycode="", protocol=selected_profile.protocol
                )

            sensor.apply_profile(selected_profile)
            return sensor

        print("Invalid selection, please try again.")