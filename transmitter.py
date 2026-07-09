#This is the network call code
#Will fill this once I've decieded on the network protocol and how to send data to the server or 



. Sensor is connected e.g. DS18B20
. Main.py runs
. Main.py calls scan all sensors in sensor detector
. Sensor detector.py requests sensor profiles.py for family code
. Main gets the family code and then knows what driver to load from the folder
. Main passes the reading interpretation to the standardiser which then formats it in SenML style and sends it to the transmitter
. transmitter.py POSTs the data to the HMI webapp