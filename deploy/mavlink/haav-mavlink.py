#!/usr/bin/env python3
"""Pixhawk (ArduPilot) MAVLink -> Prometheus metrics + journald events, for Alloy to ship to Grafana Cloud.

Reads a MAVLink stream (SiK radio, Pixhawk USB/UART, or a mavlink-router UDP endpoint), asks the autopilot
for only the messages below at low rates, and serves the latest values on http://127.0.0.1:9102/metrics.
A metric disappears once its source message goes stale, so a lost link shows up as a gap in Grafana instead
of a frozen value. haav_mav_connected is always exported.

Events (STATUSTEXT, mode changes, arm/disarm, waypoints reached, link up/down) go to stdout with syslog
"<N>" priority prefixes; systemd stores them in the journal at that level and Alloy forwards them to Loki.

Read-only by design: the only thing ever sent to the autopilot is MAV_CMD_SET_MESSAGE_INTERVAL. It never
sends HEARTBEATs, so stopping this service can't trip a GCS failsafe on the Pixhawk.

Will fold into haavd's `pixhawk` collector once haavd exists (README step 3/7).
"""
import math
import os
import threading
import time

# Must be set before pymavlink is imported: without it pymavlink starts on MAVLink 1.0 and can lock there
# (e.g. on a v1 RADIO_STATUS from the SiK radio), dropping v2 extension fields. v1 frames still parse.
os.environ.setdefault("MAVLINK20", "1")

from prometheus_client import CollectorRegistry, start_http_server
from prometheus_client.core import GaugeMetricFamily
from pymavlink import mavutil

mav = mavutil.mavlink

DEVICE = os.environ.get("HAAV_MAV_DEVICE", "/dev/ttyUSB0")
BAUD = int(os.environ.get("HAAV_MAV_BAUD", "57600"))
LISTEN = os.environ.get("HAAV_MAV_LISTEN", "127.0.0.1:9102")
SYSID = int(os.environ.get("HAAV_MAV_SYSID", "0"))  # 0 = lock onto the first autopilot heard
# "1:port,3:stbd" -> SERVO_OUTPUT_RAW servo1/servo3 exported as output="port"/"stbd".
SERVO_OUTPUTS = [
    (int(k), (v or k))
    for k, _, v in (s.strip().partition(":") for s in os.environ.get("HAAV_MAV_SERVO_OUTPUTS", "1,2,3,4").split(","))
    if k
]

HEARTBEAT_TIMEOUT_S = 5
RECHECK_EVERY_S = 30  # re-request only messages that went missing (Pixhawk reboot, another GCS turned them off)
REQUEST_SPACING_S = 0.1  # pace requests so a burst can't overflow the SiK radio's buffer

# Messages requested from the autopilot, in Hz. Alloy scrapes every 5 s, so faster is wasted radio bandwidth.
# HEARTBEAT (1 Hz), STATUSTEXT, MISSION_ITEM_REACHED, and RADIO_STATUS (from the SiK radio) arrive unasked.
RATES_HZ = {
    "GLOBAL_POSITION_INT": 1,
    "VFR_HUD": 1,
    "ATTITUDE": 1,
    "GPS_RAW_INT": 1,
    "SYS_STATUS": 1,
    "NAV_CONTROLLER_OUTPUT": 1,
    "MISSION_CURRENT": 1,
    "SERVO_OUTPUT_RAW": 1,
    "RC_CHANNELS": 1,
    "EKF_STATUS_REPORT": 0.5,
    "BATTERY_STATUS": 0.5,
    "POWER_STATUS": 0.5,
    "SCALED_PRESSURE": 0.2,
    "SYSTEM_TIME": 0.2,
}


def stale_after(msg_type):
    hz = RATES_HZ.get(msg_type)
    return max(10.0, 3.0 / hz) if hz else 10.0


def log(priority, text):
    # systemd parses the "<N>" prefix as the syslog priority (0 emerg .. 7 debug), once per line, so keep
    # everything on one line or continuation lines would fall back to INFO.
    text = str(text).replace("\r", " ").replace("\n", " ")
    print(f"<{priority}>{text}", flush=True)


SENSOR_NAMES = {
    # Most are MAV_SYS_STATUS_SENSOR_*, but newer ones are MAV_SYS_STATUS_* (AHRS, LOGGING, PREARM_CHECK...).
    bit: e.name.removeprefix("MAV_SYS_STATUS_SENSOR_").removeprefix("MAV_SYS_STATUS_").lower()
    for bit, e in mav.enums["MAV_SYS_STATUS_SENSOR"].items()
    if bit and bit & (bit - 1) == 0  # single bits only, skip the _ENUM_END entry
    and bit != mav.MAV_SYS_STATUS_EXTENSION_USED  # protocol flag, not a sensor
}


class State:
    def __init__(self):
        self.lock = threading.Lock()
        # key -> (msg, monotonic time, wall time). Key is the msg type, or "BATTERY_STATUS:<id>" since
        # ArduPilot sends one BATTERY_STATUS per monitored battery.
        self.latest = {}
        self.last_heartbeat_wall = None

    def put(self, msg, key=None):
        with self.lock:
            self.latest[key or msg.get_type()] = (msg, time.monotonic(), time.time())
            if msg.get_type() == "HEARTBEAT":
                self.last_heartbeat_wall = time.time()

    def snapshot(self):
        with self.lock:
            return dict(self.latest), self.last_heartbeat_wall


class MavCollector:
    def __init__(self, state):
        self.state = state

    def describe(self):
        return []

    def collect(self):
        latest, last_hb_wall = self.state.snapshot()
        now = time.monotonic()
        families = {}

        def fresh(key):
            entry = latest.get(key)
            if entry and now - entry[1] <= stale_after(key.partition(":")[0]):
                return entry[0]
            return None

        def out(name, doc, value, **labels):
            if value is None or (isinstance(value, float) and math.isnan(value)):
                return
            fam = families.get(name)
            if fam is None:
                fam = families[name] = GaugeMetricFamily(f"haav_mav_{name}", doc, labels=list(labels))
            fam.add_metric(list(labels.values()), float(value))

        def unless(value, unknown, scale=1.0):
            return None if value == unknown else value * scale

        hb = latest.get("HEARTBEAT")
        connected = hb is not None and now - hb[1] <= HEARTBEAT_TIMEOUT_S
        out("connected", "1 if an autopilot HEARTBEAT arrived in the last 5 s.", int(connected))
        if last_hb_wall:
            out("last_heartbeat_timestamp_seconds", "Unix time of the last autopilot HEARTBEAT.", last_hb_wall)

        if m := fresh("HEARTBEAT"):
            out("armed", "1 if the vehicle is armed.", int(bool(m.base_mode & mav.MAV_MODE_FLAG_SAFETY_ARMED)))
            out("mode", "Current flight mode (value is always 1).", 1, mode=mavutil.mode_string_v10(m))
            out("system_status", "MAV_STATE (3 standby, 4 active, 5 critical, 6 emergency).", m.system_status)

        if (m := fresh("GLOBAL_POSITION_INT")) and (m.lat or m.lon):
            out("gps_lat", "Fused latitude, degrees.", m.lat / 1e7)
            out("gps_lon", "Fused longitude, degrees.", m.lon / 1e7)

        if m := fresh("VFR_HUD"):
            out("groundspeed_mps", "Ground speed, m/s.", m.groundspeed)
            out("heading_deg", "Heading, degrees (0..360).", m.heading)
            out("throttle_percent", "Throttle output, percent.", m.throttle)

        if m := fresh("GPS_RAW_INT"):
            out("gps_fix_type", "GPS fix (0-1 none, 2 2D, 3 3D, 4 DGPS, 5 RTK float, 6 RTK fixed).", m.fix_type)
            out("gps_sats", "Satellites visible.", unless(m.satellites_visible, 255))
            out("gps_hdop", "GPS horizontal dilution of precision.", unless(m.eph, 65535, 0.01))

        if m := fresh("EKF_STATUS_REPORT"):
            out("ekf_flags", "EKF_STATUS_FLAGS bitmask.", m.flags)
            out("ekf_variance", "EKF normalized variance (>0.8 is bad, >1 fails checks).", m.velocity_variance, kind="velocity")
            out("ekf_variance", "", m.pos_horiz_variance, kind="pos_horiz")
            out("ekf_variance", "", m.compass_variance, kind="compass")

        if m := fresh("SYS_STATUS"):
            out("battery_volts", "Pixhawk power-module battery voltage.", unless(m.voltage_battery, 65535, 0.001))
            out("battery_amps", "Pixhawk power-module battery current.", unless(m.current_battery, -1, 0.01))
            out("battery_remaining_ratio", "Autopilot-estimated battery remaining (0..1).", unless(m.battery_remaining, -1, 0.01))
            watched = m.onboard_control_sensors_present & m.onboard_control_sensors_enabled
            for bit, name in SENSOR_NAMES.items():
                if watched & bit:
                    out("sensor_healthy", "1 if this enabled sensor reports healthy.",
                        int(bool(m.onboard_control_sensors_health & bit)), sensor=name)

        for key in sorted(k for k in latest if k.startswith("BATTERY_STATUS:")):
            if m := fresh(key):
                out("battery_consumed_mah", "Charge drawn since boot, mAh.", unless(m.current_consumed, -1), battery=str(m.id))

        if m := fresh("POWER_STATUS"):
            out("vcc_volts", "Pixhawk 5 V rail.", m.Vcc / 1000)

        if m := fresh("ATTITUDE"):
            out("roll_deg", "Roll (heel), degrees.", math.degrees(m.roll))
            out("pitch_deg", "Pitch (trim), degrees.", math.degrees(m.pitch))

        if m := fresh("SCALED_PRESSURE"):
            out("board_temp_celsius", "Barometer temperature (inside the Pixhawk).", m.temperature / 100)

        if m := fresh("SERVO_OUTPUT_RAW"):  # port 0 only; Reader drops other ports
            for idx, name in SERVO_OUTPUTS:
                pwm = getattr(m, f"servo{idx}_raw", 0)
                out("servo_pwm", "PWM sent to the output, microseconds.", pwm or None, output=name)

        if m := fresh("RC_CHANNELS"):
            out("rc_rssi", "RC receiver signal (0..254).", unless(m.rssi, 255))

        if m := fresh("RADIO_STATUS"):
            out("radio_rssi", "SiK radio local RSSI (raw units).", unless(m.rssi, 255))
            out("radio_remrssi", "SiK radio remote RSSI (raw units).", unless(m.remrssi, 255))
            for field in ("noise", "remnoise", "txbuf"):
                out(f"radio_{field}", f"SiK radio {field}.", getattr(m, field))
            out("radio_rxerrors", "SiK radio receive errors (wraps at 65535).", m.rxerrors)
            out("radio_fixed", "SiK radio packets corrected by error correction (wraps at 65535).", m.fixed)

        if m := fresh("MISSION_CURRENT"):
            out("mission_seq", "Current mission item.", m.seq)

        if m := fresh("NAV_CONTROLLER_OUTPUT"):
            out("wp_dist_m", "Distance to the active waypoint, m.", m.wp_dist)
            out("xtrack_error_m", "Cross-track error, m.", m.xtrack_error)

        entry = latest.get("SYSTEM_TIME")
        if entry and now - entry[1] <= stale_after("SYSTEM_TIME") and entry[0].time_unix_usec:
            out("clock_offset_seconds", "Pi clock minus autopilot GPS time (positive = Pi ahead).",
                entry[2] - entry[0].time_unix_usec / 1e6)

        yield from families.values()


class Reader:
    def __init__(self, state):
        self.state = state
        self.target_sys = SYSID or None
        self.target_comp = 1
        self.link_up = False
        self.last_hb = 0.0
        self.last_check = 0.0
        self.last_send = 0.0
        self.pending = []  # message names still to request, sent one per REQUEST_SPACING_S
        self.armed = None
        self.mode = None

    def queue_missing(self, everything=False):
        latest, _ = self.state.snapshot()
        now = time.monotonic()
        for name in RATES_HZ:
            entry = latest.get(name)
            if (everything or not entry or now - entry[1] > stale_after(name)) and name not in self.pending:
                self.pending.append(name)
        self.last_check = now

    def send_next_request(self, conn):
        if not self.pending or time.monotonic() - self.last_send < REQUEST_SPACING_S:
            return
        name = self.pending.pop(0)
        conn.mav.command_long_send(
            self.target_sys, self.target_comp, mav.MAV_CMD_SET_MESSAGE_INTERVAL, 0,
            getattr(mav, f"MAVLINK_MSG_ID_{name}"), int(1e6 / RATES_HZ[name]), 0, 0, 0, 0, 0)
        self.last_send = time.monotonic()

    def handle(self, conn, msg):
        mtype = msg.get_type()
        if mtype == "BAD_DATA":
            return
        if mtype == "RADIO_STATUS":  # comes from the radio itself (its own sysid)
            self.state.put(msg)
            return

        src, comp = msg.get_srcSystem(), msg.get_srcComponent()
        is_vehicle_hb = mtype == "HEARTBEAT" and conn.probably_vehicle_heartbeat(msg)
        if self.target_sys is None:
            if not is_vehicle_hb:
                return  # GCS, radio, gimbal, or companion; wait for a real autopilot
            self.target_sys, self.target_comp = src, comp
            log(6, f"locked onto autopilot sysid {src} compid {comp}")
        # Only the autopilot itself: a gimbal/camera/companion sharing the sysid must not overwrite its metrics.
        if src != self.target_sys or comp != self.target_comp:
            return

        if mtype == "HEARTBEAT":
            if not is_vehicle_hb:
                return
            self.last_hb = time.monotonic()
            if not self.link_up:
                self.link_up = True
                self.failures = 0
                log(5, "autopilot link up")
                self.queue_missing(everything=True)
            armed = bool(msg.base_mode & mav.MAV_MODE_FLAG_SAFETY_ARMED)
            mode = mavutil.mode_string_v10(msg)
            if self.armed is not None and armed != self.armed:
                log(5, "ARMED" if armed else "DISARMED")
            if self.mode is not None and mode != self.mode:
                log(5, f"mode {self.mode} -> {mode}")
            self.armed, self.mode = armed, mode
        elif mtype == "STATUSTEXT":
            # MAV_SEVERITY uses the same 0..7 scale as syslog.
            log(min(max(msg.severity, 0), 7), f"STATUSTEXT {msg.text}")
            return
        elif mtype == "MISSION_ITEM_REACHED":
            log(6, f"waypoint {msg.seq} reached")
            return
        elif mtype == "BATTERY_STATUS":
            self.state.put(msg, key=f"BATTERY_STATUS:{msg.id}")
            return
        elif mtype == "SERVO_OUTPUT_RAW" and msg.port != 0:
            return  # outputs 17+ (port 1) would otherwise replace outputs 1-16

        self.state.put(msg)

    def tick(self, conn):
        now = time.monotonic()
        if self.link_up and now - self.last_hb > HEARTBEAT_TIMEOUT_S:
            self.link_up = False
            log(4, f"autopilot link lost (no HEARTBEAT for {HEARTBEAT_TIMEOUT_S} s)")
        if self.link_up:
            if now - self.last_check > RECHECK_EVERY_S:
                self.queue_missing()
            self.send_next_request(conn)

    def run(self):
        open_failed = False
        self.failures = 0  # consecutive read failures without a HEARTBEAT in between
        while True:
            try:
                conn = mavutil.mavlink_connection(
                    DEVICE, baud=BAUD, source_system=255,
                    source_component=mav.MAV_COMP_ID_ONBOARD_COMPUTER)
            except Exception as e:  # device unplugged / missing; log once, keep retrying quietly
                if not open_failed:
                    log(3, f"cannot open {DEVICE}: {e} (retrying every 5 s)")
                    open_failed = True
                time.sleep(5)
                continue
            if self.failures == 0:
                log(6, f"reading MAVLink from {DEVICE} @ {BAUD} baud")
            open_failed = False
            try:
                while True:
                    # Wake often while requests are queued so they go out paced, not only when messages arrive.
                    msg = conn.recv_match(blocking=True, timeout=REQUEST_SPACING_S if self.pending else 1)
                    if msg is not None:
                        self.handle(conn, msg)
                    self.tick(conn)
            except Exception as e:
                # Back off 2 s -> 60 s and log only the first failure of a streak, so a flaky adapter
                # can't flood the journal (and the LTE link via Loki).
                if self.failures == 0:
                    log(4, f"read from {DEVICE} failed: {e}; reopening with backoff")
                self.failures += 1
                self.link_up = False
                self.pending.clear()
                try:
                    conn.close()
                except Exception:
                    pass
                time.sleep(min(60, 2 ** self.failures))


def main():
    state = State()
    registry = CollectorRegistry()  # no python_*/process_* default metrics; they'd waste LTE data
    registry.register(MavCollector(state))
    host, _, port = LISTEN.rpartition(":")
    start_http_server(int(port), addr=host or "127.0.0.1", registry=registry)
    log(6, f"serving metrics on http://{LISTEN}/metrics")
    Reader(state).run()


if __name__ == "__main__":
    main()
