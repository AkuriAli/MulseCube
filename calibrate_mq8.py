from drivers import mq8_esp32_driver

print("Calibrating MQ-8 in clean air...")
print("Make sure there are no gas sources nearby, and the sensor has stabilized.\n")

ro = mq8_esp32_driver.calibrate_ro(samples=50)

if ro is None:
    print("Calibration failed - check the ESP32 connection.")
else:
    print(f"\nDone. RO_CLEAN_AIR = {ro}")
    print("Copy this value into mq8_esp32_driver.py's RO_CLEAN_AIR constant.")