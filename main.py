import time

from sensor_detector import auto_detect_sensors, manual_fallback_selection
from data_publisher import DataPublisher

WEBAPP_URL = "http://localhost:5000/api/sensor-data"


def run_sensor_loop(sensor, publisher):
    """
    Reads a Sensor on a loop and publishes each reading.
    Works for any sensor model, analog or digital, single or multi-measurement -
    the differences are all handled inside Sensor/SensorProfile already.
    """
    print(f"\nRunning: {sensor.profile.model} ({'Analog' if sensor.is_analog else 'Digital'})")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            readings = sensor.read_value()

            if not readings:
                print("Values not available (sensor not detected or read failed).")
            else:
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

            time.sleep(2)

    except KeyboardInterrupt:
        print("\nStopped by user.")


def main():
    publisher = DataPublisher(WEBAPP_URL)

    print("Scanning for connected sensors...")
    detected_sensors = auto_detect_sensors()

    if detected_sensors:
        print(f"\n{len(detected_sensors)} sensor(s) auto-detected:")
        for s in detected_sensors:
            print(f" - {s}")
    else:
        print("\nNo sensors could be auto-identified.")
        fallback_sensor = manual_fallback_selection()
        if fallback_sensor:
            detected_sensors = [fallback_sensor]

    if not detected_sensors:
        print("\nNo sensor to run. Exiting.")
        return

    # For now: run the first sensor found/selected.
    # (Running multiple simultaneously is a reasonable next step once this works.)
    run_sensor_loop(detected_sensors[0], publisher)


if __name__ == "__main__":
    main()