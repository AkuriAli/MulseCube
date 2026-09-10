from sensor import Sensor
from profile_registry import find_by_family_code, get_manual_registration_profiles, get_all_profiles
from drivers import ds18b20_driver
from gpio_scanner import scan_gpio_connections


def auto_detect_sensors():
    """
    Scans every protocol capable of self-identification and builds a Sensor
    object (with its profile already attached) for each match found.
    """
    identified = []

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

    # --- I2C scan: TODO once BME280 is wired up ---

    return identified


def _build_sensor_for_pin(profile, pin):
    """Builds a Sensor for a manually-confirmed profile on a specific GPIO pin."""
    if profile.is_analog:
        sensor = Sensor.analog(name=profile.model, gnd=None, vcc=None, pincount=1)
    else:
        sensor = Sensor.digital(name=profile.model, family=profile.model,
                                familycode="", protocol=profile.protocol)

    device_path = profile.resolve_address_fn(pin)
    sensor.device_address = device_path
    sensor.gpio_ports = [pin]
    sensor.apply_profile(profile)
    return sensor


def prompt_for_gpio_sensors(detected_pins):
    """
    Walks through each GPIO pin that showed a physical connection, asking
    which sensor is wired there. Offers "same as previous port" for a
    single sensor that spans more than one GPIO pin.
    """
    profiles = get_manual_registration_profiles()
    sensors = []
    last_sensor = None

    for pin in detected_pins:
        print(f"\nConnection detected on GPIO{pin}. Which sensor is this?")
        for i, profile in enumerate(profiles, start=1):
            print(f"{i}. {profile.model}  ({profile.protocol})")

        same_as_previous_option = None
        if last_sensor:
            same_as_previous_option = len(profiles) + 1
            print(f"{same_as_previous_option}. Same as previous port ({last_sensor.profile.model})")

        print("0. Skip this port")

        while True:
            choice = input("\nEnter number: ").strip()

            if choice == "0":
                break

            if choice.isdigit() and 1 <= int(choice) <= len(profiles):
                profile = profiles[int(choice) - 1]
                sensor = _build_sensor_for_pin(profile, pin)
                sensors.append(sensor)
                last_sensor = sensor
                break

            if same_as_previous_option and choice == str(same_as_previous_option):
                # This pin belongs to the sensor just registered, not a new one
                last_sensor.gpio_ports.append(pin)
                break

            print("Invalid selection, please try again.")

    return sensors


def manual_fallback_selection():
    """
    Last-resort manual add, for anything the GPIO presence scan might miss
    (e.g. a sensor that idles LOW instead of HIGH).
    """
    profiles = get_all_profiles()

    print("\nAdd a sensor manually:")
    for i, profile in enumerate(profiles, start=1):
        print(f"{i}. {profile.model}  ({profile.protocol})")
    print("0. Cancel")

    while True:
        choice = input("\nEnter number: ").strip()

        if choice == "0":
            return None

        if choice.isdigit() and 1 <= int(choice) <= len(profiles):
            profile = profiles[int(choice) - 1]
            if profile.is_analog:
                sensor = Sensor.analog(name=profile.model, gnd=None, vcc=None, pincount=1)
            else:
                sensor = Sensor.digital(name=profile.model, family=profile.model,
                                        familycode="", protocol=profile.protocol)
            sensor.apply_profile(profile)
            return sensor

        print("Invalid selection, please try again.")