#!/bin/sh
# Writes Raspberry Pi firmware health (throttling, under-voltage, clock, core voltage) as Prometheus
# metrics for Alloy's textfile collector. node_exporter can't see these; only the VideoCore firmware can.
# Run every 15 s by pi-health.timer.
set -eu

OUT_DIR=${OUT_DIR:-/var/lib/haav/textfile}
OUT="$OUT_DIR/pi_health.prom"
TMP="$OUT.$$"

# e.g. "throttled=0x50005"
raw=$(vcgencmd get_throttled | cut -d= -f2)
flags=$((raw))

bit() { echo $(( (flags >> $1) & 1 )); }

# e.g. "frequency(48)=1500000000" and "volt=0.8500V"
arm_hz=$(vcgencmd measure_clock arm | cut -d= -f2)
core_v=$(vcgencmd measure_volts core | cut -d= -f2 | tr -d V)

cat > "$TMP" <<EOF
# HELP haav_pi_throttled_flags Raw bitmask from vcgencmd get_throttled.
# TYPE haav_pi_throttled_flags gauge
haav_pi_throttled_flags $flags
# HELP haav_pi_throttle_state 1 if the condition is active now (when="now") or has occurred since boot (when="since_boot").
# TYPE haav_pi_throttle_state gauge
haav_pi_throttle_state{condition="under_voltage",when="now"} $(bit 0)
haav_pi_throttle_state{condition="freq_capped",when="now"} $(bit 1)
haav_pi_throttle_state{condition="throttled",when="now"} $(bit 2)
haav_pi_throttle_state{condition="soft_temp_limit",when="now"} $(bit 3)
haav_pi_throttle_state{condition="under_voltage",when="since_boot"} $(bit 16)
haav_pi_throttle_state{condition="freq_capped",when="since_boot"} $(bit 17)
haav_pi_throttle_state{condition="throttled",when="since_boot"} $(bit 18)
haav_pi_throttle_state{condition="soft_temp_limit",when="since_boot"} $(bit 19)
# HELP haav_pi_arm_clock_hz Current ARM core clock.
# TYPE haav_pi_arm_clock_hz gauge
haav_pi_arm_clock_hz $arm_hz
# HELP haav_pi_core_volts Current SoC core voltage.
# TYPE haav_pi_core_volts gauge
haav_pi_core_volts $core_v
EOF

# Atomic replace so Alloy never reads a half-written file.
chmod 644 "$TMP"
mv "$TMP" "$OUT"
