"""
Small building blocks used by the sensor monitor: turning "something was found"
into a Sensor object, and the dashboard questions for manually-added sensors.
"""
from sensor import Sensor
from profile_registry import get_manual_registration_profiles
import remote_selector

SKIP_OPTION = "Skip this port"
CANCEL_OPTION = "Cancel"


def model_label(profile):
    return f"{profile.model} ({profile.protocol})"


def build_1wire_sensor(profile, device_address):
    sensor = Sensor.digital(
        name=profile.model,
        family=profile.model,
        familycode=device_address.split("-")[0],
        protocol=profile.protocol,
        device_address=device_address,
    )
    sensor.apply_profile(profile)
    return sensor


def build_i2c_sensor(profile, address):
    sensor = Sensor.digital(
        name=profile.model,
        family=profile.model,
        familycode=hex(address),
        protocol=profile.protocol,
        device_address=address,
    )
    sensor.apply_profile(profile)
    return sensor


def build_gpio_sensor(profile, pin):
    """
    Sets up a sensor on a GPIO pin. Returns None if the pin couldn't be set up
    (for a DHT that means the kernel overlay failed to load).
    """
    address = profile.resolve_address_fn(pin)
    if address is None:
        return None

    if profile.is_analog:
        sensor = Sensor.analog(name=profile.model, gnd=None, vcc=None, pincount=1)
    else:
        sensor = Sensor.digital(name=profile.model, family=profile.model,
                                familycode="", protocol=profile.protocol)

    sensor.device_address = address
    sensor.gpio_ports = [pin]
    sensor.apply_profile(profile)
    return sensor


def choose_manual_sensor(free_pins):
    """
    Asks, on the dashboard, which model to add and which GPIO pin it is on.
    Blocks until answered, so call it from a worker thread. Returns
    (profile, pin), or None if cancelled.
    """
    profiles = get_manual_registration_profiles()
    options = [model_label(p) for p in profiles] + [CANCEL_OPTION]

    choice = remote_selector.request_selection(
        request_id="manual-model",
        prompt="Add a sensor manually - which model?",
        options=options,
    )
    if choice is None or choice == CANCEL_OPTION:
        return None

    profile = next((p for p in profiles if model_label(p) == choice), None)
    if profile is None:
        return None

    pin_choice = remote_selector.request_selection(
        request_id="manual-pin",
        prompt=f"Which GPIO pin is the {profile.model}'s data/DO pin connected to?",
        options=[str(p) for p in free_pins] + [CANCEL_OPTION],
    )
    if pin_choice is None or pin_choice == CANCEL_OPTION:
        return None

    return profile, int(pin_choice)
