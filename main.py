import time

from sensor_detector import (
    auto_detect_sensors,
    prompt_for_gpio_sensors,
    manual_fallback_selection,
)
from gpio_scanner import scan_gpio_connections
from data_publisher import DataPublisher

WEBAPP_URL = "http://localhost:5000/api/sensor-data"


def build_sensor_list():
    print("Scanning for connected sensors...")
    sensors = auto_detect_sensors()

    if sensors:
        print(f"\n{len(sensors)} sensor(s) auto-detected:")
        for s in sensors:
            print(f" - {s}")
    else:
        print("\nNo sensors could be auto-identified via protocol scanning.")

    print("\nChecking GPIO pins for additional connections...")
    detected_pins = scan_gpio_connections()

    if detected_pins:
        print(f"Connections found on: {', '.join(f'GPIO{p}' for p in detected_pins)}")
        sensors.extend(prompt_for_gpio_sensors(detected_pins))
    else:
        print("No additional GPIO connections detected.")

    while True:
        choice = input("\nAdd another sensor manually? (y/n): ").strip().lower()
        if choice != "y":
            break
        sensor = manual_fallback_selection()
        if sensor:
            sensors.append(sensor)

    return sensors


def run_multi_sensor_loop(sensors, publisher):
    print(f"\nRunning {len(sensors)} sensor(s):")
    for s in sensors:
        print(f" - {s}")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            for sensor in sensors:
                readings = sensor.read_value()

                if not readings:
                    print(f"  {sensor.profile.model}: values not available.")
                    continue

                for measurement_type, value in readings.items():
                    measurement = sensor.profile.get_measurement(measurement_type)
                    unit = measurement.unit if measurement else ""
                    print(f"  {sensor.profile.model} {measurement_type}: {value}{unit}")

                    reading_record = {
                        "model": sensor.profile.model,
                        "type": measurement_type,
                        "unit": unit,
                        "value": value,
                        "timestamp": time.time(),
                    }
                    publisher.publish(reading_record)

            print()
            time.sleep(2)

    except KeyboardInterrupt:
        print("\nStopped by user.")


def main():
    publisher = DataPublisher(WEBAPP_URL)
    sensors = build_sensor_list()

    if not sensors:
        print("\nNo sensors to run. Exiting.")
        return

    run_multi_sensor_loop(sensors, publisher)


if __name__ == "__main__":
    main()