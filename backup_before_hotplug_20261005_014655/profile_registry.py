import importlib
import pkgutil
import profiles


def _discover_profiles():
    """
    Automatically imports every module inside the profiles/ package and
    collects each one's PROFILE object. Adding a new sensor means adding
    one new file to profiles/ - this function requires zero changes.
    A profile that fails to import is skipped with a warning, not fatal.
    """
    discovered = []
    for _, module_name, _ in pkgutil.iter_modules(profiles.__path__):
        try:
            module = importlib.import_module(f"profiles.{module_name}")
        except Exception as e:
            # One broken sensor file (a missing library, a misplaced driver)
            # must not stop every other sensor from working.
            print(f"WARNING: skipping profile '{module_name}': {type(e).__name__}: {e}")
            continue
        if hasattr(module, "PROFILE"):
            discovered.append(module.PROFILE)
    return discovered


PROFILES = _discover_profiles()


def find_by_family_code(family_code):
    for profile in PROFILES:
        if profile.identifier_type == "family_code" and profile.identifier == family_code:
            return profile
    return None


def find_by_i2c_address(address):
    for profile in PROFILES:
        if profile.identifier_type == "i2c_address" and profile.identifier == address:
            return profile
    return None


def get_manual_registration_profiles():
    return [p for p in PROFILES if p.identifier_type == "manual_gpio"]


def get_all_profiles():
    return PROFILES