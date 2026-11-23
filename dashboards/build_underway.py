#!/usr/bin/env python3
"""Generates dashboards/underway.json ("Haav underway", uid haav-pi). Edit here, then:
    python3 dashboards/build_underway.py dashboards/underway.json
and import/overwrite in Grafana (or ask Claude to push it via the Grafana MCP)."""
import json, sys
from panels import *

panels = []
# ---------------- Vehicle tiles (8 x w3) --------------------------------
y = 0
NOPX = "No Pixhawk link"
panels += [
    stat("Pixhawk link", [t(f'max(haav_mav_connected{{{MAV}}})', instant=True)], g(0, y, 3, 4),
         mappings=vmap({"1": ("LINK", "green"), "0": ("LOST", "red")}), th=thresholds((None, "red")),
         novalue="NO EXPORTER", desc="Autopilot HEARTBEAT seen in the last 5 s (haav-mavlink). NO EXPORTER = haav-mavlink not running or LTE down."),
    stat("Armed", [t(f'max(haav_mav_armed{{{MAV}}})', instant=True)], g(3, y, 3, 4),
         mappings=vmap({"0": ("DISARMED", "blue"), "1": ("ARMED", "orange")}), th=thresholds((None, "blue")), novalue=NOPX),
    stat("Mode", [t(f'max by (mode) (haav_mav_mode{{{MAV}}})', "{{mode}}", instant=True)], g(6, y, 3, 4),
         th=thresholds((None, "purple")), novalue=NOPX),
    stat("Battery", [t(f'max(haav_mav_battery_volts{{{MAV}}})', instant=True)], g(9, y, 3, 4), unit="volt", decimals=1,
         th=thresholds((None, "red"), (42, "orange"), (44.4, "green")), novalue=NOPX,
         desc="Pixhawk power-module voltage. Thresholds assume the 12S pack: red < 3.5 V/cell (42 V), orange < 3.7 V/cell (44.4 V)."),
    stat("Speed", [t(f'max(haav_mav_groundspeed_mps{{{MAV}}}) * 2.23694', instant=True)], g(12, y, 3, 4), unit="velocitymph",
         decimals=1, th=thresholds((None, "text")), color_mode="value", novalue=NOPX,
         desc="GPS ground speed. Race pace: 2 mi in 45 min = 2.7 mph minimum."),
    stat("GPS sats (3D fix)", [t(f'max(haav_mav_gps_sats{{{MAV}}}) * max(haav_mav_gps_fix_type{{{MAV}}} >= bool 3)', instant=True)],
         g(15, y, 3, 4), th=thresholds((None, "red"), (6, "orange"), (9, "green")), novalue=NOPX,
         mappings=vmap({"0": ("NO FIX", "red")}), desc="Satellites in view; 0 / NO FIX if the fix is worse than 3D."),
    stat("Sensors", [t(f'count(haav_mav_sensor_healthy{{{MAV}}} == 0) or (min(haav_mav_sensor_healthy{{{MAV}}}) * 0)', instant=True)],
         g(18, y, 3, 4), th=thresholds((None, "green"), (1, "red")), novalue=NOPX,
         mappings=vmap({"0": ("ALL OK", "green")}), desc="Number of enabled Pixhawk sensors reporting unhealthy (SYS_STATUS). Check Pixhawk diagnostics for which."),
    stat("To waypoint", [t(f'max(haav_mav_wp_dist_m{{{MAV}}})', instant=True)], g(21, y, 3, 4), unit="lengthm",
         th=thresholds((None, "text")), color_mode="value", novalue=NOPX),
]
# ---------------- Pi tiles (6 x w4) -------------------------------------
y = 4
FREE = f'min(node_filesystem_avail_bytes{{mountpoint="/",{NODE}}} / node_filesystem_size_bytes{{mountpoint="/",{NODE}}})'
RO = f'max(node_filesystem_readonly{{mountpoint="/",{NODE}}})'
UV = lambda w: f'max(haav_pi_throttle_state{{condition="under_voltage",when="{w}",{H}}})'
panels += [
    stat("LTE: last sample", [t(f'time() - max(timestamp(up{{{NODE}}}))', instant=True)], g(0, y, 4, 4), unit="s", decimals=0,
         th=thresholds((None, "green"), (60, "orange"), (180, "red")), novalue="> 5 min",
         desc="Age of the newest Pi sample in Grafana Cloud. Climbs during LTE drops; the 8 h WAL backfills afterwards."),
    stat("Pi 5 V rail", [t(f'2 * {UV("now")} + {UV("since_boot")}', instant=True)], g(4, y, 4, 4),
         mappings=vmap({"0": ("OK", "green"), "1": ("SAGGED EARLIER", "orange"), "2": ("SAGGING NOW", "red"), "3": ("SAGGING NOW", "red")}),
         th=thresholds((None, "green")), desc="vcgencmd under-voltage: 5 V rail below ~4.63 V. SAGGED EARLIER = happened since boot (e.g. under motor load)."),
    stat("Pi SoC temp", [t(f'max(node_thermal_zone_temp{{{NODE}}})', instant=True)], g(8, y, 4, 4), unit="celsius", decimals=1,
         th=thresholds((None, "green"), (70, "orange"), (80, "red")), desc="Pi 4 soft-throttles at 80 °C, hard-throttles at 85 °C."),
    stat("SD card", [t(f'{FREE} * (1 - {RO}) - {RO}', instant=True)], g(12, y, 4, 4), unit="percentunit", decimals=0,
         mappings=[rmap(-2, -0.5, "READ-ONLY", "red")], th=thresholds((None, "red"), (0, "red"), (0.1, "orange"), (0.2, "green")),
         desc="Free space on /. READ-ONLY = kernel remounted the card after errors (sudden power loss); logging has stopped."),
    stat("LTE data (time range)",
         [t(f'sum(increase(node_network_receive_bytes_total{{device="wwan0",{NODE}}}[$__range]) + increase(node_network_transmit_bytes_total{{device="wwan0",{NODE}}}[$__range]))', instant=True)],
         g(16, y, 4, 4), unit="bytes", decimals=1, th=thresholds((None, "text")), color_mode="value", novalue="No LTE modem",
         desc="rx + tx through wwan0 over the dashboard's time range. Set the range to the run to see its data cost."),
    stat("LTE burn rate (1h)",
         [t(f'sum(rate(node_network_receive_bytes_total{{device="wwan0",{NODE}}}[1h]) + rate(node_network_transmit_bytes_total{{device="wwan0",{NODE}}}[1h])) * 3600', instant=True)],
         g(20, y, 4, 4), unit="bytes", decimals=1, th=thresholds((None, "green"), (20e6, "orange"), (50e6, "red")), novalue="No LTE modem",
         desc="Bytes per hour on wwan0 over the last hour. Budget: 10-20 MB/hr against the ~500 MB SIM."),
]
# ---------------- Map + status timeline ---------------------------------
y = 8
panels.append({
    "type": "geomap", "title": "Track", "id": nid(), "datasource": PROM, "gridPos": g(0, y, 12, 11),
    "description": "Pixhawk fused position over the time range; arrow points the way the boat is heading.",
    "targets": [t(f'max(haav_mav_gps_lat{{{MAV}}})', "lat"), t(f'max(haav_mav_gps_lon{{{MAV}}})', "lon", ref="B")],
    "transformations": [{"id": "joinByField", "options": {"byField": "Time", "mode": "inner"}}],
    "fieldConfig": {"defaults": {"color": {"mode": "fixed", "fixedColor": "orange"}}, "overrides": []},
    "options": {
        "view": {"id": "fit", "lat": 43.07, "lon": -89.4, "zoom": 15, "allLayers": True, "lastOnly": False, "padding": 20},
        "controls": {"showZoom": True, "mouseWheelZoom": True, "showAttribution": True, "showScale": True},
        "basemap": {"type": "default", "name": "Basemap"},
        "layers": [
            {"type": "route", "name": "Track", "config": {"style": {"color": {"fixed": "orange"}, "opacity": 0.8, "size": {"fixed": 3}},
                                                         "arrow": 1},
             "location": {"mode": "coords", "latitude": "lat", "longitude": "lon"}, "tooltip": True},
        ],
        "tooltip": {"mode": "details"}},
})
bad01 = lambda name, txt: {"matcher": {"id": "byName", "options": name}, "properties": [
    {"id": "mappings", "value": vmap({"0": ("ok", "green"), "1": (txt, "red")})}]}
panels.append({
    "type": "state-timeline", "title": "Status timeline", "id": nid(), "datasource": PROM, "gridPos": g(12, y, 12, 11),
    "description": "Gaps = no data (LTE down, Pi off, or Pixhawk link lost).",
    "targets": [
        t(f'max(haav_mav_connected{{{MAV}}})', "Pixhawk link"),
        t(f'max(haav_mav_armed{{{MAV}}})', "Armed", ref="B"),
        t(f'count(haav_mav_sensor_healthy{{{MAV}}} == 0) > bool 0 or (min(haav_mav_sensor_healthy{{{MAV}}}) * 0)', "Pixhawk sensors", ref="C"),
        t(f'max(haav_mav_gps_fix_type{{{MAV}}} < bool 3)', "GPS fix", ref="D"),
        t(UV("now"), "Pi 5 V rail", ref="E"),
        t(f'max(haav_pi_throttle_state{{condition=~"throttled|soft_temp_limit",when="now",{H}}})', "Pi throttling", ref="F"),
    ],
    "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "thresholds": thresholds((None, "green")),
                                 "custom": {"fillOpacity": 85, "lineWidth": 0}},
                    "overrides": [
                        {"matcher": {"id": "byName", "options": "Pixhawk link"},
                         "properties": [{"id": "mappings", "value": vmap({"1": ("link", "green"), "0": ("LOST", "red")})}]},
                        {"matcher": {"id": "byName", "options": "Armed"},
                         "properties": [{"id": "mappings", "value": vmap({"0": ("disarmed", "blue"), "1": ("ARMED", "orange")})}]},
                        bad01("Pixhawk sensors", "UNHEALTHY"), bad01("GPS fix", "NO 3D FIX"),
                        bad01("Pi 5 V rail", "UNDER-VOLTAGE"), bad01("Pi throttling", "THROTTLED")]},
    "options": {"showValue": "never", "mergeValues": True, "rowHeight": 0.85, "alignValue": "left",
                "legend": {"showLegend": False}, "tooltip": {"mode": "single"}}})
# ---------------- Charts --------------------------------------------------
y += 11
right_axis = lambda name, unit, lo=None, hi=None: {"matcher": {"id": "byName", "options": name}, "properties": [
    {"id": "custom.axisPlacement", "value": "right"}, {"id": "unit", "value": unit}]
    + ([{"id": "min", "value": lo}] if lo is not None else []) + ([{"id": "max", "value": hi}] if hi is not None else [])}
panels += [
    ts("Speed & throttle", [t(f'max(haav_mav_groundspeed_mps{{{MAV}}}) * 2.23694', "speed"),
                            t(f'max(haav_mav_throttle_percent{{{MAV}}})', "throttle", ref="B")],
       g(0, y, 12, 8), unit="velocitymph", minv=0, overrides=[right_axis("throttle", "percent", 0, 100)]),
    ts("Battery", [t(f'max(haav_mav_battery_volts{{{MAV}}})', "volts"), t(f'max(haav_mav_battery_amps{{{MAV}}})', "amps", ref="B")],
       g(12, y, 12, 8), unit="volt", th=thresholds((None, "transparent"), (42, "red")), th_style="off",
       overrides=[right_axis("amps", "amp", 0)], legend_calcs=["lastNotNull", "min", "max"]),
]
y += 8
panels += [
    ts("Navigation", [t(f'max(haav_mav_wp_dist_m{{{MAV}}})', "to waypoint"),
                      t(f'max(abs(haav_mav_xtrack_error_m{{{MAV}}}))', "cross-track error", ref="B"),
                      t(f'max(haav_mav_mission_seq{{{MAV}}})', "waypoint #", ref="C")],
       g(0, y, 12, 8), unit="lengthm", minv=0, overrides=[right_axis("waypoint #", "none", 0)]),
    ts("Temperatures", [t(f'max(node_thermal_zone_temp{{{NODE}}})', "Pi SoC"),
                        t(f'max(haav_mav_board_temp_celsius{{{MAV}}})', "Pixhawk board", ref="B")],
       g(12, y, 12, 8), unit="celsius", th=thresholds((None, "transparent"), (80, "orange"), (85, "red")),
       th_style="line+area", legend_calcs=["lastNotNull", "max"], desc="Bands: Pi 4 soft limit 80 °C, hard throttle 85 °C."),
]
y += 8
panels += [
    ts("LTE throughput (wwan0)",
       [t(f'max(rate(node_network_receive_bytes_total{{device="wwan0",{NODE}}}[$__rate_interval]))', "rx"),
        t(f'max(rate(node_network_transmit_bytes_total{{device="wwan0",{NODE}}}[$__rate_interval]))', "tx", ref="B")],
       g(0, y, 12, 7), unit="Bps", overrides=[{"matcher": {"id": "byName", "options": "tx"},
                                               "properties": [{"id": "custom.transform", "value": "negative-Y"}]}],
       desc="rx up, tx down. Mostly Alloy remote_write; flat zero = modem up but nothing flowing."),
    ts("Control link", [t(f'max(haav_mav_radio_rssi{{{MAV}}})', "SiK rssi (local)"),
                        t(f'max(haav_mav_radio_remrssi{{{MAV}}})', "SiK rssi (remote)", ref="B"),
                        t(f'max(haav_mav_rc_rssi{{{MAV}}})', "RC receiver", ref="C")],
       g(12, y, 12, 7), minv=0, maxv=255, desc="Raw 0-255 signal values. Watch for a downward trend as the boat moves away."),
]
y += 7
panels.append({"type": "logs", "title": "Events & warnings", "id": nid(), "datasource": LOKI, "gridPos": g(0, y, 24, 10),
               "description": "haav-mavlink events (Pixhawk STATUSTEXT, mode/arm changes, waypoints, link up/down) and WARNING+ from kernel, ModemManager, tailscaled, alloy.",
               "targets": [t(f'{{job="systemd-journal",{H}}}', ds=LOKI)],
               "options": {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending",
                           "enableLogDetails": True, "prettifyLogMessage": False, "dedupStrategy": "none"}})
y += 10
# ---------------- Collapsed diagnostics -----------------------------------
cy = y + 1
pi_diag = [
    stat("Uptime", [t(f'time() - max(node_boot_time_seconds{{{NODE}}})', instant=True)], g(0, cy, 4, 4), unit="dtdurations",
         th=thresholds((None, "orange"), (600, "green"))),
    stat("Memory used", [t(f'1 - max(node_memory_MemAvailable_bytes{{{NODE}}}) / max(node_memory_MemTotal_bytes{{{NODE}}})', instant=True)],
         g(4, cy, 4, 4), unit="percentunit", decimals=0, th=thresholds((None, "green"), (0.8, "orange"), (0.9, "red"))),
    stat("Any throttle since boot", [t(f'max(haav_pi_throttle_state{{when="since_boot",{H}}})', instant=True)], g(8, cy, 4, 4),
         mappings=vmap({"0": ("NO", "green"), "1": ("YES", "orange")}), th=thresholds((None, "green"))),
    stat("pi-health script", [t(f'max(node_textfile_scrape_error{{{NODE}}}) + absent(haav_pi_throttled_flags{{{H}}}) or max(node_textfile_scrape_error{{{NODE}}})', instant=True)],
         g(12, cy, 4, 4), mappings=[{"type": "value", "options": {"0": {"text": "OK", "color": "green", "index": 0}}}, rmap(1, 99, "BROKEN", "red")],
         th=thresholds((None, "green"), (1, "red"))),
    stat("ARM clock", [t(f'max(haav_pi_arm_clock_hz{{{H}}})', instant=True)], g(16, cy, 4, 4), unit="hertz", th=thresholds((None, "text")), color_mode="value"),
    stat("Core voltage", [t(f'max(haav_pi_core_volts{{{H}}})', instant=True)], g(20, cy, 4, 4), unit="volt", decimals=3, th=thresholds((None, "text")), color_mode="value"),
    ts("CPU usage by mode",
       [t(f'sum by (mode) (rate(node_cpu_seconds_total{{mode!="idle",{NODE}}}[$__rate_interval])) / scalar(count(count by (cpu) (node_cpu_seconds_total{{{NODE}}})))', "{{mode}}")],
       g(0, cy + 4, 8, 7), unit="percentunit", minv=0, maxv=1, stack=True, desc="iowait spikes usually mean a slow/dying SD card."),
    ts("Load average", [t(f'max(node_load1{{{NODE}}})', "1m"), t(f'max(node_load5{{{NODE}}})', "5m", ref="B"),
                        t(f'max(node_load15{{{NODE}}})', "15m", ref="C")], g(8, cy + 4, 8, 7), minv=0),
    ts("Memory", [t(f'max(node_memory_MemTotal_bytes{{{NODE}}}) - max(node_memory_MemAvailable_bytes{{{NODE}}})', "used"),
                  t(f'max(node_memory_MemTotal_bytes{{{NODE}}})', "total", ref="B")], g(16, cy + 4, 8, 7), unit="bytes", minv=0),
    ts("Disk free", [t(f'max by (mountpoint) (node_filesystem_avail_bytes{{{NODE}}})', "{{mountpoint}}")], g(0, cy + 11, 12, 7), unit="bytes", minv=0),
    ts("All interfaces (rx up, tx down)",
       [t(f'max by (device) (rate(node_network_receive_bytes_total{{{NODE}}}[$__rate_interval]))', "{{device}} rx"),
        t(f'max by (device) (rate(node_network_transmit_bytes_total{{{NODE}}}[$__rate_interval]))', "{{device}} tx", ref="B")],
       g(12, cy + 11, 12, 7), unit="Bps", overrides=[{"matcher": {"id": "byRegexp", "options": ".* tx"},
                                                       "properties": [{"id": "custom.transform", "value": "negative-Y"}]}]),
]
panels.append(collapsed("Pi diagnostics", y, pi_diag)); y += 1
px_diag = [
    ts("EKF variance", [t(f'max by (kind) (haav_mav_ekf_variance{{{MAV}}})', "{{kind}}")], g(0, cy, 8, 7), minv=0,
       th=thresholds((None, "transparent"), (0.5, "orange"), (0.8, "red")), th_style="line", desc=">0.8 is bad, >1 fails EKF checks."),
    ts("GPS quality", [t(f'max(haav_mav_gps_sats{{{MAV}}})', "sats"), t(f'max(haav_mav_gps_hdop{{{MAV}}})', "hdop", ref="B")],
       g(8, cy, 8, 7), minv=0, overrides=[right_axis("hdop", "none", 0)]),
    {**ts("Sensor health", [t(f'max by (sensor) (haav_mav_sensor_healthy{{{MAV}}})', "{{sensor}}")], g(16, cy, 8, 7)), "type": "state-timeline",
     "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "thresholds": thresholds((None, "red"), (1, "green")),
                                  "mappings": vmap({"1": ("ok", "green"), "0": ("UNHEALTHY", "red")}),
                                  "custom": {"fillOpacity": 85, "lineWidth": 0}}, "overrides": []},
     "options": {"showValue": "never", "mergeValues": True, "rowHeight": 0.85, "legend": {"showLegend": False}}},
    ts("Attitude", [t(f'max(haav_mav_roll_deg{{{MAV}}})', "roll"), t(f'max(haav_mav_pitch_deg{{{MAV}}})', "pitch", ref="B")],
       g(0, cy + 7, 8, 7), unit="degree"),
    ts("Motor outputs", [t(f'max by (output) (haav_mav_servo_pwm{{{MAV}}})', "{{output}}")], g(8, cy + 7, 8, 7), unit="µs",
       desc="PWM per named output (HAAV_MAV_SERVO_OUTPUTS). Diverging port/stbd = turning."),
    ts("Pixhawk 5 V rail & clock offset", [t(f'max(haav_mav_vcc_volts{{{MAV}}})', "Pixhawk Vcc"),
                                           t(f'max(haav_mav_clock_offset_seconds{{{MAV}}})', "Pi − GPS clock", ref="B")],
       g(16, cy + 7, 8, 7), unit="volt", overrides=[right_axis("Pi − GPS clock", "s")]),
    ts("SiK radio errors", [t(f'max(haav_mav_radio_noise{{{MAV}}})', "noise (local)"), t(f'max(haav_mav_radio_remnoise{{{MAV}}})', "noise (remote)", ref="B"),
                            t(f'max(haav_mav_radio_txbuf{{{MAV}}})', "tx buffer %", ref="C")], g(0, cy + 14, 24, 6)),
]
panels.append(collapsed("Pixhawk diagnostics", y, px_diag)); y += 1

dash = {
    "uid": "haav-pi", "title": "Haav underway", "tags": ["haav", "raspberry-pi", "pixhawk"],
    "description": "What to watch while the boat is running. Source: haav-telemetry/dashboards/underway.json (generated).",
    "timezone": "browser", "refresh": "10s", "schemaVersion": 39, "editable": True, "graphTooltip": 1,
    "time": {"from": "now-30m", "to": "now"},
    "timepicker": {"refresh_intervals": ["5s", "10s", "30s", "1m", "5m"]},
    "links": [link("Pi details", "haav-pi-detail", "info")],
    "templating": {"list": datasource_vars()},
    "annotations": {"list": [
        {"name": "Pi reboots", "enable": True, "iconColor": "orange", "datasource": PROM,
         "expr": f'changes(node_boot_time_seconds{{{NODE}}}[2m]) > 0', "step": "1m", "titleFormat": "Pi rebooted", "textFormat": "{{host}}"},
        {"name": "Arm / disarm", "enable": True, "iconColor": "red", "datasource": PROM,
         "expr": f'changes(haav_mav_armed{{{MAV}}}[30s]) > 0', "step": "15s", "titleFormat": "Arm state changed", "textFormat": "{{host}}"},
    ]},
    "panels": panels,
}
json.dump(dash, open(sys.argv[1], "w"), indent=2)
print(len(panels), "top-level panels")
