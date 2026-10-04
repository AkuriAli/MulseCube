import smbus2
import time

I2C_BUS_NUMBER = 1
DHT20_ADDRESS = 0x38


def read_raw(device_address=None):
    """
    Reads temperature (C) and humidity (%) from a DHT20 over I2C, following
    the AHT20/DHT20 datasheet protocol: check calibration status, trigger a
    measurement, wait, then read and decode the 20-bit packed result.
    device_address is accepted for interface consistency with other drivers
    but unused - DHT20 always lives at the fixed address 0x38.
    Returns (temperature, humidity), or (None, None) on failure.
    """
    try:
        bus = smbus2.SMBus(I2C_BUS_NUMBER)

        # Check calibration status bit; initialize if not yet calibrated
        status_read = smbus2.i2c_msg.read(DHT20_ADDRESS, 1)
        bus.i2c_rdwr(status_read)
        status = list(status_read)[0]
        if not (status & 0x08):
            init_write = smbus2.i2c_msg.write(DHT20_ADDRESS, [0xBE, 0x08, 0x00])
            bus.i2c_rdwr(init_write)
            time.sleep(0.01)

        # Trigger a measurement
        trigger_write = smbus2.i2c_msg.write(DHT20_ADDRESS, [0xAC, 0x33, 0x00])
        bus.i2c_rdwr(trigger_write)
        time.sleep(0.09)   # datasheet: wait at least 80ms

        # Read the 7-byte result: [status, H, H, H|T, T, T, CRC]
        result_read = smbus2.i2c_msg.read(DHT20_ADDRESS, 7)
        bus.i2c_rdwr(result_read)
        data = list(result_read)

        bus.close()
    except OSError:
        return None, None

    raw_humidity = (data[1] << 12) | (data[2] << 4) | (data[3] >> 4)
    raw_temperature = ((data[3] & 0x0F) << 16) | (data[4] << 8) | data[5]

    humidity = (raw_humidity / (1 << 20)) * 100
    temperature = (raw_temperature / (1 << 20)) * 200 - 50

    return round(temperature, 1), round(humidity, 1)