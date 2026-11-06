"""
Your port wiring. Edit this to match your schematic, then change
USE_PRESENCE_PINS to True and restart:   sudo systemctl restart mulsecube-main

Each port has three things:
  presence     the GPIO of the port's presence wire. The plug joins this wire to ground, so a
               plugged-in port reads LOW. The Pi's own built-in pull-up makes an empty port
               read HIGH - no resistor and no extra wire is needed.
  data         the GPIO of the port's data wire (DHT, DS18B20, ...)
  adc_channel  which ESP32 analog input that same data wire also feeds (0, 1 or 2), so an
               analog sensor such as the MQ-8 can be read from any port.

While USE_PRESENCE_PINS is False, the program behaves as before: it looks at the pins
themselves to guess when something is plugged in.
"""

USE_PRESENCE_PINS = True

PORTS = [
    {"name": "Port 1", "presence": 9,  "data": 23, "adc_channel": 0},
    {"name": "Port 2", "presence": 10, "data": 24, "adc_channel": 1},
    {"name": "Port 3", "presence": 11, "data": 25, "adc_channel": 2},
]
