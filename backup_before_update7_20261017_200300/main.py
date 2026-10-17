import signal
import sys

from data_publisher import DataPublisher
from remote_selector import wait_for_hmi
from sensor_monitor import SensorMonitor
from drivers import dht_driver

WEBAPP_URL = "http://localhost:5000/api/sensor-data"


def main():
    wait_for_hmi()

    # Start from a clean slate: remove any DHT overlays left over from a
    # previous run, which would otherwise keep the kernel holding those pins.
    dht_driver.cleanup_stale_overlays()

    publisher = DataPublisher(WEBAPP_URL)
    monitor = SensorMonitor(publisher)

    # systemd stops a service with SIGTERM, which would normally kill Python
    # instantly. Turn it into a normal exit so the cleanup below always runs.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    try:
        monitor.run_forever()
    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        monitor.shutdown()
        dht_driver.cleanup_stale_overlays()


if __name__ == "__main__":
    main()
