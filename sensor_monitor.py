import queue
import threading
import time

import gpio_scanner
import remote_selector
from drivers import ds18b20_driver, i2c_bus_driver
from profile_registry import (
    find_by_family_code,
    get_i2c_profiles,
    get_manual_registration_profiles,
)
from sensor_detector import (
    SKIP_OPTION,
    build_1wire_sensor,
    build_gpio_sensor,
    build_i2c_sensor,
    choose_manual_sensor,
    model_label,
)


class SensorMonitor:
    """
    Keeps watching for sensors being plugged in and unplugged, like a PC does
    with USB devices, so nothing needs restarting to change sensors.

    How each kind of sensor is noticed:
      * I2C (e.g. DHT20) and 1-Wire (e.g. DS18B20) identify themselves, so they
        are added automatically and removed when they stop answering.
      * GPIO sensors (DHT11/DHT22, ...) can't identify themselves. A pin that
        reads HIGH is "something is plugged in here", and the dashboard asks
        which sensor it is. Such a sensor counts as unplugged after several
        reads in a row fail.
      * An MQ-8 can't be noticed being unplugged by software at all - its signal
        pin may idle either HIGH or LOW. That needs the planned presence-wire
        hardware. (Add it with the "Add sensor manually" button.)
    """

    CYCLE_SECONDS = 2               # how often sensors are read
    GPIO_SCAN_INTERVAL = 4          # how often the GPIO pins are checked
    UNPLUG_SCANS = 2                # LOW scans in a row = a waiting/skipped pin was unplugged
    SELF_ID_MISSES = 2              # missed checks in a row = an I2C/1-Wire sensor is gone
    READ_FAILURES_BEFORE_REMOVE = 5 # failed reads in a row = a GPIO sensor is gone

    def __init__(self, publisher):
        self.publisher = publisher
        self.sensors = {}           # key -> Sensor.  key = ("1wire", addr) | ("i2c", addr) | ("gpio", pin)
        self.misses = {}            # I2C / 1-Wire key -> consecutive checks where it didn't answer
        self.read_failures = {}     # GPIO key -> consecutive failed reads (kept separate on purpose:
                                    # a good read must never hide a sensor that stopped answering scans)
        self.pending = {}           # pin -> {"cancel": Event}   (a question is open for this pin)
        self.skipped = set()        # pins the user chose "Skip" for while plugged in
        self.low_counts = {}        # pin -> consecutive LOW scans (only tracked for pending/skipped pins)
        self.answers = queue.Queue()        # (pin, token, choice) from prompt threads
        self.manual_results = queue.Queue() # (profile, pin) from the manual-add thread
        self.manual_thread = None
        self._last_gpio_scan = 0.0

    # ------------------------------------------------------------ helpers --

    def _describe(self, key):
        sensor = self.sensors.get(key)
        model = sensor.profile.model if sensor and sensor.profile else "sensor"
        kind, where = key
        if kind == "gpio":
            return f"{model} on GPIO{where}"
        if kind == "i2c":
            return f"{model} on I2C {hex(where)}"
        return f"{model} on 1-Wire {where}"

    def _owned_pins(self):
        return {where for (kind, where) in self.sensors if kind == "gpio"}

    def _free_pins(self):
        owned = self._owned_pins()
        return [p for p in gpio_scanner.CANDIDATE_PINS if p not in owned]

    def _add(self, key, sensor):
        self.sensors[key] = sensor
        self.misses.pop(key, None)
        self.read_failures.pop(key, None)
        print(f"+ Sensor connected: {self._describe(key)}")

    def _remove(self, key, reason, notify=True):
        description = self._describe(key)
        sensor = self.sensors.pop(key, None)
        self.misses.pop(key, None)
        self.read_failures.pop(key, None)
        if sensor is None:
            return

        for pin in sensor.gpio_ports:
            try:
                sensor.profile.release_fn(pin)
            except Exception as e:
                print(f"  WARNING: could not release GPIO{pin}: {e}")

        # Forget skip/low memory so the pin is treated as brand new next time
        for pin in sensor.gpio_ports:
            self.skipped.discard(pin)
            self.low_counts.pop(pin, None)

        print(f"- Sensor disconnected: {description} ({reason})")
        if notify:
            remote_selector.notify_sensor_removed(sensor.profile.model)

    # -------------------------------------------- I2C and 1-Wire (automatic) --

    def _sync_self_identifying(self):
        present = {}

        for address in ds18b20_driver.scan_for_ds18b20():
            profile = find_by_family_code(address.split("-")[0])
            if profile:
                present[("1wire", address)] = (profile, address)

        i2c_profiles = get_i2c_profiles()
        if i2c_profiles:
            for address in i2c_bus_driver.probe(list(i2c_profiles)):
                present[("i2c", address)] = (i2c_profiles[address], address)

        for key, (profile, address) in present.items():
            self.misses.pop(key, None)
            if key not in self.sensors:
                kind = key[0]
                sensor = (build_1wire_sensor(profile, address) if kind == "1wire"
                          else build_i2c_sensor(profile, address))
                self._add(key, sensor)

        for key in [k for k in self.sensors if k[0] in ("1wire", "i2c")]:
            if key not in present:
                self.misses[key] = self.misses.get(key, 0) + 1
                if self.misses[key] >= self.SELF_ID_MISSES:
                    self._remove(key, "no longer detected")

    # ------------------------------------------------ GPIO pins (ask the user) --

    def _scan_gpio(self):
        readings = gpio_scanner.scan_pins(self._free_pins())

        for pin, high in readings.items():
            if high:
                self.low_counts.pop(pin, None)
                if pin not in self.pending and pin not in self.skipped:
                    self._open_prompt(pin)
            elif pin in self.pending or pin in self.skipped:
                count = self.low_counts.get(pin, 0) + 1
                self.low_counts[pin] = count
                if count >= self.UNPLUG_SCANS:
                    self._forget_pin(pin)

    def _forget_pin(self, pin):
        """The pin went quiet: withdraw any open question and reset what we remember about it."""
        entry = self.pending.pop(pin, None)
        if entry is not None:
            entry["cancel"].set()
            print(f"  GPIO{pin} was unplugged before it was identified.")
        self.skipped.discard(pin)
        self.low_counts.pop(pin, None)

    def _open_prompt(self, pin):
        cancel = threading.Event()
        self.pending[pin] = {"cancel": cancel}

        profiles = get_manual_registration_profiles()
        options = [model_label(p) for p in profiles] + [SKIP_OPTION]
        prompt = f"Something was plugged into GPIO{pin}. Which sensor is it?"
        print(f"? Something detected on GPIO{pin}")

        def ask():
            choice = remote_selector.request_selection(
                request_id=f"gpio-port-{pin}",
                prompt=prompt,
                options=options,
                cancel_event=cancel,
            )
            self.answers.put((pin, cancel, choice))

        threading.Thread(target=ask, daemon=True).start()

    def _process_answers(self):
        while True:
            try:
                pin, token, choice = self.answers.get_nowait()
            except queue.Empty:
                return

            entry = self.pending.get(pin)
            if entry is None or entry["cancel"] is not token:
                continue            # stale answer for a prompt that no longer applies
            del self.pending[pin]

            if choice is None:
                continue            # cancelled, or the dashboard was unreachable
            if choice == SKIP_OPTION:
                self.skipped.add(pin)
                continue

            profile = next((p for p in get_manual_registration_profiles()
                            if model_label(p) == choice), None)
            if profile is None:
                continue
            self._connect_gpio_sensor(profile, pin)

    def _connect_gpio_sensor(self, profile, pin):
        sensor = build_gpio_sensor(profile, pin)
        if sensor is None:
            # Remember it so we don't ask again every scan; unplug and replug to retry
            self.skipped.add(pin)
            print(f"  Could not set up {profile.model} on GPIO{pin} - "
                  f"unplug and replug it to try again.")
            return
        self._add(("gpio", pin), sensor)

    # ------------------------------------------- manual add (dashboard button) --

    def _check_manual_request(self):
        if self.manual_thread is not None and self.manual_thread.is_alive():
            return
        if not remote_selector.poll_manual_add_request():
            return

        free_pins = self._free_pins()

        def ask():
            self.manual_results.put(choose_manual_sensor(free_pins))

        self.manual_thread = threading.Thread(target=ask, daemon=True)
        self.manual_thread.start()

    def _process_manual_results(self):
        while True:
            try:
                result = self.manual_results.get_nowait()
            except queue.Empty:
                return
            if result is None:
                continue

            profile, pin = result
            if ("gpio", pin) in self.sensors or pin in self.pending:
                print(f"  GPIO{pin} is already in use - manual add ignored.")
                continue
            self.skipped.discard(pin)
            self._connect_gpio_sensor(profile, pin)

    # ------------------------------------------------------ reading sensors --

    def _read_and_publish(self):
        for key, sensor in list(self.sensors.items()):
            try:
                readings = sensor.read_value()
            except Exception as e:
                print(f"  {sensor.profile.model}: read error ({e})")
                readings = None

            if not readings:
                print(f"  {sensor.profile.model}: values not available.")
                if key[0] == "gpio":
                    self.read_failures[key] = self.read_failures.get(key, 0) + 1
                    if self.read_failures[key] >= self.READ_FAILURES_BEFORE_REMOVE:
                        self._remove(key, "stopped responding")
                continue

            self.read_failures.pop(key, None)
            for measurement_type, value in readings.items():
                measurement = sensor.profile.get_measurement(measurement_type)
                unit = measurement.unit if measurement else ""
                print(f"  {sensor.profile.model} {measurement_type}: {value}{unit}")

                self.publisher.publish({
                    "model": sensor.profile.model,
                    "type": measurement_type,
                    "unit": unit,
                    "value": value,
                    "timestamp": time.time(),
                    "min": measurement.min_value if measurement else None,
                    "max": measurement.max_value if measurement else None,
                })

    # ------------------------------------------------------------- main loop --

    def step(self):
        """One pass: look for changes, then read everything that is connected."""
        self._sync_self_identifying()
        self._process_answers()
        self._process_manual_results()

        now = time.time()
        if now - self._last_gpio_scan >= self.GPIO_SCAN_INTERVAL:
            self._scan_gpio()
            self._last_gpio_scan = time.time()

        self._check_manual_request()
        self._read_and_publish()

    def run_forever(self):
        print("Watching for sensors - plug and unplug freely. Press Ctrl+C to stop.\n")
        while True:
            started = time.time()
            self.step()
            print()
            time.sleep(max(0.2, self.CYCLE_SECONDS - (time.time() - started)))

    def shutdown(self):
        """Hand every pin back so the next start begins from a clean slate."""
        for entry in self.pending.values():
            entry["cancel"].set()
        for key in list(self.sensors):
            self._remove(key, "shutting down", notify=False)
