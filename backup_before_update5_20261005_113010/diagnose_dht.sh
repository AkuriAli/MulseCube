#!/bin/bash
# Prints everything needed to work out why a DHT sensor isn't giving readings.
#
# Run it WHILE the problem is happening (sensor plugged in, no values showing):
#     sudo bash diagnose_dht.sh
# then copy everything it prints and send it over.

line() { echo; echo "=== $1 ==="; }

line "1. Are the services running?"
for s in mulsecube-hmi mulsecube-main; do
  printf "%-18s %s\n" "$s" "$(systemctl is-active $s 2>/dev/null || echo unknown)"
done

line "2. DHT overlays currently loaded (dtoverlay -l)"
dtoverlay -l 2>&1 || echo "(dtoverlay command failed)"

line "3. DHT lines written in config.txt (these can't be removed without a reboot)"
grep -n "dht11" /boot/firmware/config.txt 2>/dev/null || echo "(none - good)"

line "4. Sensor devices the kernel has created, and where each one lives"
found=0
for d in /sys/bus/iio/devices/iio:device*; do
  [ -e "$d" ] || continue
  found=1
  echo "$(basename "$d") -> $(readlink -f "$d")   name: $(cat "$d/name" 2>/dev/null)"
done
[ $found -eq 0 ] && echo "(no sensor devices exist - no overlay is loaded)"

line "5. Reading each device directly (this is exactly what the program does)"
for d in /sys/bus/iio/devices/iio:device*; do
  [ -e "$d" ] || continue
  for f in in_temp_input in_humidityrelative_input; do
    printf "%s %s: " "$(basename "$d")" "$f"
    if out=$(timeout 6 cat "$d/$f" 2>&1); then echo "$out"; else echo "FAILED -> $out"; fi
  done
done

line "6. Kernel messages mentioning the sensor"
dmesg 2>/dev/null | grep -i -E "dht11|dht22" | tail -15 || true
echo "(end of kernel messages)"

line "7. What the sensor program has been saying lately"
journalctl -u mulsecube-main -n 30 --no-pager 2>/dev/null || echo "(couldn't read the log)"

echo
echo "=== done - copy everything above and send it ==="
