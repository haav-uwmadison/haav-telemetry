#!/usr/bin/env python3
"""Generates dashboards/pi.json ("Haav Pi", uid haav-pi-detail): the drill-down behind the Pi tiles on
"Haav underway". Edit here, then:
    python3 dashboards/build_pi.py dashboards/pi.json
and import/overwrite in Grafana (or ask Claude to push it via the Grafana MCP)."""
import json, sys
from panels import *

SIM_CAP_BYTES = 500e6  # 1NCE lifetime data cap (README constraint 3)
IFACE_TX = {"matcher": {"id": "byRegexp", "options": ".* (tx|write)"},
            "properties": [{"id": "custom.transform", "value": "negative-Y"}]}
right_axis = lambda name, unit, lo=None, hi=None: {"matcher": {"id": "byName", "options": name}, "properties": [
    {"id": "custom.axisPlacement", "value": "right"}, {"id": "unit", "value": unit}]
    + ([{"id": "min", "value": lo}] if lo is not None else []) + ([{"id": "max", "value": hi}] if hi is not None else [])}
UV = lambda w: f'max(haav_pi_throttle_state{{condition="under_voltage",when="{w}",{H}}})'
LTE_RATE = (f'sum(rate(node_network_receive_bytes_total{{device="wwan0",{NODE}}}[1h]) '
            f'+ rate(node_network_transmit_bytes_total{{device="wwan0",{NODE}}}[1h]))')

panels = []
y = 0
# ---------------- Health ---------------------------------------------------
panels.append(row("Health", y)); y += 1
panels += [
    stat("Uptime", [t(f'time() - max(node_boot_time_seconds{{{NODE}}})', instant=True)], g(0, y, 4, 4), unit="dtdurations",
         th=thresholds((None, "orange"), (600, "green"))),
    stat("Last sample", [t(f'time() - max(timestamp(up{{{NODE}}}))', instant=True)], g(4, y, 4, 4), unit="s", decimals=0,
         th=thresholds((None, "green"), (60, "orange"), (180, "red")), novalue="> 5 min"),
    stat("Scrape time", [t(f'max(scrape_duration_seconds{{{NODE}}})', instant=True)], g(8, y, 4, 4), unit="s", decimals=2,
         th=thresholds((None, "green"), (1, "orange"), (5, "red")),
         desc="How long Alloy's node_exporter takes to collect. Slow = Pi overloaded or a hung /sys read."),
    stat("pi-health script", [t(f'max(node_textfile_scrape_error{{{NODE}}}) + absent(haav_pi_throttled_flags{{{H}}}) or max(node_textfile_scrape_error{{{NODE}}})', instant=True)],
         g(12, y, 4, 4), mappings=[{"type": "value", "options": {"0": {"text": "OK", "color": "green", "index": 0}}}, rmap(1, 99, "BROKEN", "red")],
         th=thresholds((None, "green"), (1, "red")), desc="Red if haav-pi-health.timer stopped or its textfile is unparseable."),
    stat("Processes running", [t(f'max(node_procs_running{{{NODE}}})', instant=True)], g(16, y, 4, 4),
         th=thresholds((None, "green"), (9, "orange"), (16, "red")), desc="Runnable tasks (includes the collectors themselves; ~5-7 is normal idle). Sustained double digits = contention."),
    stat("Reboots in range", [t(f'sum(changes(node_boot_time_seconds{{{NODE}}}[$__range]))', instant=True)], g(20, y, 4, 4),
         th=thresholds((None, "green"), (1, "orange")), desc="Boot-time changes in the selected range (brownouts, kill switch, battery swaps)."),
]
y += 4
# ---------------- Power & firmware -----------------------------------------
panels.append(row("Power & firmware", y)); y += 1
def flags(title, when, x, color, desc):
    p = stat(title, [t(f'max by (condition) (haav_pi_throttle_state{{when="{when}",{H}}})', "{{condition}}", instant=True)],
             g(x, y, 9, 4), mappings=vmap({"0": ("no", "green"), "1": ("YES", color)}), th=thresholds((None, "green"), (1, color)), desc=desc)
    p["options"].update(textMode="value_and_name", orientation="vertical", text={"titleSize": 13, "valueSize": 26})
    return p
panels += [
    flags("Throttle flags: now", "now", 0, "red", "vcgencmd get_throttled bits 0-3: active this second."),
    flags("Throttle flags: since boot", "since_boot", 9, "orange", "vcgencmd get_throttled bits 16-19: happened at any point since boot (latched until reboot)."),
    stat("Raw get_throttled", [t(f'max(haav_pi_throttled_flags{{{H}}})', instant=True)], g(18, y, 6, 4), unit="hex0x",
         th=thresholds((None, "green"), (1, "orange")), desc="Bits 0-3 now, 16-19 since boot. 0x50000 = under-voltage + throttled earlier."),
]
y += 4
panels.append({
    "type": "state-timeline", "title": "Throttle conditions over time", "id": nid(), "datasource": PROM, "gridPos": g(0, y, 12, 8),
    "targets": [t(f'max by (condition) (haav_pi_throttle_state{{when="now",{H}}})', "{{condition}}")],
    "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "thresholds": thresholds((None, "green"), (1, "red")),
                                 "mappings": vmap({"0": ("ok", "green"), "1": ("ACTIVE", "red")}),
                                 "custom": {"fillOpacity": 85, "lineWidth": 0}}, "overrides": []},
    "options": {"showValue": "never", "mergeValues": True, "rowHeight": 0.85, "legend": {"showLegend": False}, "tooltip": {"mode": "single"}}})
panels += [
    ts("Temperature vs ARM clock", [t(f'max(node_thermal_zone_temp{{{NODE}}})', "SoC temp"),
                                    t(f'max(haav_pi_arm_clock_hz{{{H}}})', "ARM clock", ref="B")],
       g(12, y, 12, 8), unit="celsius", th=thresholds((None, "transparent"), (80, "orange"), (85, "red")), th_style="line",
       overrides=[{**right_axis("ARM clock", "hertz", 0), "properties": right_axis("ARM clock", "hertz", 0)["properties"] + [{"id": "custom.thresholdsStyle", "value": {"mode": "off"}}]}],
       desc="Clock drops while temp is high = thermal throttling. Clock drops with normal temp = under-voltage or just idle (ondemand governor)."),
]
y += 8
panels += [
    ts("Core voltage", [t(f'max(haav_pi_core_volts{{{H}}})', "core")], g(0, y, 12, 6), unit="volt",
       desc="SoC core rail set by the firmware's DVFS. Rises with clock; sudden dips alongside under-voltage point at the 5 V supply."),
    ts("Under-voltage (now vs since boot)", [t(UV("now"), "now"), t(UV("since_boot"), "since boot", ref="B")], g(12, y, 12, 6),
       minv=0, maxv=1, desc="'since boot' stepping to 1 while 'now' blips = a brief sag, e.g. motors spinning up on a shared 5 V converter."),
]
panels[-1]["fieldConfig"]["defaults"]["custom"].update(drawStyle="line", lineInterpolation="stepAfter", fillOpacity=20)
y += 6
# ---------------- CPU & memory ---------------------------------------------
panels.append(row("CPU & memory", y)); y += 1
CORES = f'scalar(count(count by (cpu) (node_cpu_seconds_total{{{NODE}}})))'
panels += [
    ts("Per-core usage", [t(f'1 - max by (cpu) (rate(node_cpu_seconds_total{{mode="idle",{NODE}}}[$__rate_interval]))', "core {{cpu}}")],
       g(0, y, 12, 8), unit="percentunit", minv=0, maxv=1,
       desc="One pegged core with the rest idle = a single-threaded process spinning (e.g. a stuck collector)."),
    ts("CPU by mode", [t(f'sum by (mode) (rate(node_cpu_seconds_total{{mode!="idle",{NODE}}}[$__rate_interval])) / {CORES}', "{{mode}}")],
       g(12, y, 12, 8), unit="percentunit", minv=0, maxv=1, stack=True,
       desc="Share of all cores. iowait = waiting on the SD card."),
]
y += 8
panels += [
    ts("Load vs cores", [t(f'max(node_load1{{{NODE}}})', "1m"), t(f'max(node_load5{{{NODE}}})', "5m", ref="B"),
                         t(f'max(node_load15{{{NODE}}})', "15m", ref="C"), t(f'{CORES} + 0 * max(node_load1{{{NODE}}})', "cores", ref="D"),
                         t(f'max(node_procs_running{{{NODE}}})', "procs running", ref="E")],
       g(0, y, 12, 8), minv=0,
       overrides=[{"matcher": {"id": "byName", "options": "cores"}, "properties": [
                       {"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [10, 10]}}, {"id": "custom.fillOpacity", "value": 0},
                       {"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]},
                  {"matcher": {"id": "byName", "options": "procs running"}, "properties": [
                       {"id": "custom.drawStyle", "value": "points"}, {"id": "custom.pointSize", "value": 3},
                       {"id": "color", "value": {"mode": "fixed", "fixedColor": "purple"}}]}],
       desc="Load above the dashed line = work queuing. On a Pi, load also counts tasks stuck in uninterruptible SD I/O."),
    ts("Memory", [t(f'max(node_memory_MemTotal_bytes{{{NODE}}}) - max(node_memory_MemAvailable_bytes{{{NODE}}})', "used"),
                  t(f'max(node_memory_MemAvailable_bytes{{{NODE}}})', "available", ref="B"),
                  t(f'max(node_memory_MemTotal_bytes{{{NODE}}})', "total", ref="C")],
       g(12, y, 12, 8), unit="bytes", minv=0, legend_calcs=["lastNotNull", "max"],
       overrides=[{"matcher": {"id": "byName", "options": "total"}, "properties": [
           {"id": "custom.fillOpacity", "value": 0}, {"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [10, 10]}},
           {"id": "color", "value": {"mode": "fixed", "fixedColor": "text"}}]}],
       desc="'used' creeping up over hours with no restart = a leak (haav-mavlink, haavd)."),
]
y += 8
# ---------------- Storage --------------------------------------------------
panels.append(row("Storage (SD card)", y)); y += 1
FILL = (f'max by (mountpoint) (node_filesystem_avail_bytes{{{NODE}}}) '
        f'/ clamp_min(-max by (mountpoint) (deriv(node_filesystem_avail_bytes{{{NODE}}}[6h])), 1e-9)')
days = stat("Time until full", [t(FILL, "{{mountpoint}}", instant=True)], g(10, y, 8, 7), unit="dtdurations",
            mappings=[rmap(31536000, 1e30, "not filling", "green")], th=thresholds((None, "red"), (6 * 3600, "orange"), (3 * 86400, "green")),
            desc="Free space ÷ how fast it shrank over the last 6 h. Reads low for a few hours after an apt install/update; trust it when it stays low (runaway logs or WAL).")
days["options"].update(textMode="value_and_name")
ro = stat("Read-only", [t(f'max by (mountpoint) (node_filesystem_readonly{{{NODE}}})', "{{mountpoint}}", instant=True)], g(18, y, 6, 7),
          mappings=vmap({"0": ("rw", "green"), "1": ("READ-ONLY", "red")}), th=thresholds((None, "green"), (1, "red")),
          desc="Kernel remounts / read-only after card errors (common after sudden power loss).")
ro["options"].update(textMode="value_and_name")
panels += [
    ts("Free space per mount", [t(f'max by (mountpoint) (node_filesystem_avail_bytes{{{NODE}}})', "{{mountpoint}}")], g(0, y, 10, 7),
       unit="bytes", minv=0, legend_calcs=["lastNotNull", "min"]),
    days, ro,
]
y += 7
SD = f'device="mmcblk0",{NODE}'
panels += [
    ts("SD throughput (read up, write down)", [t(f'max(rate(node_disk_read_bytes_total{{{SD}}}[$__rate_interval]))', "read"),
                                               t(f'max(rate(node_disk_written_bytes_total{{{SD}}}[$__rate_interval]))', "write", ref="B")],
       g(0, y, 10, 7), unit="Bps", overrides=[IFACE_TX], desc="Needs the diskstats collector (added to config.alloy 2026-11-23)."),
    ts("SD busy & IOPS", [t(f'max(rate(node_disk_io_time_seconds_total{{{SD}}}[$__rate_interval]))', "busy"),
                          t(f'max(rate(node_disk_reads_completed_total{{{SD}}}[$__rate_interval]))', "read IOPS", ref="B"),
                          t(f'max(rate(node_disk_writes_completed_total{{{SD}}}[$__rate_interval]))', "write IOPS", ref="C")],
       g(10, y, 8, 7), unit="percentunit", minv=0, th=thresholds((None, "transparent"), (0.8, "red")), th_style="line",
       overrides=[right_axis("read IOPS", "iops", 0), right_axis("write IOPS", "iops", 0)],
       desc="Busy near 100% with low throughput = the card is struggling (worn or failing). Best early warning before it goes read-only."),
    stat("Written in range", [t(f'sum(increase(node_disk_written_bytes_total{{{SD}}}[$__range]))', instant=True)], g(18, y, 6, 7),
         unit="bytes", decimals=1, th=thresholds((None, "text")), color_mode="value", novalue="No disk metrics yet",
         desc="SD wear indicator. Journald + Alloy WAL are the main writers."),
]
y += 7
# ---------------- Network & LTE --------------------------------------------
panels.append(row("Network & LTE", y)); y += 1
table = {
    "type": "table", "title": "Bytes per interface (time range)", "id": nid(), "datasource": PROM, "gridPos": g(12, y, 12, 8),
    "targets": [dict(t(f'sum by (device) (increase(node_network_receive_bytes_total{{{NODE}}}[$__range]))', instant=True), format="table"),
                dict(t(f'sum by (device) (increase(node_network_transmit_bytes_total{{{NODE}}}[$__range]))', ref="B", instant=True), format="table")],
    "transformations": [
        {"id": "joinByField", "options": {"byField": "device", "mode": "outer"}},
        {"id": "organize", "options": {"excludeByName": {"Time": True, "Time 1": True, "Time 2": True},
                                       "renameByName": {"device": "Interface", "Value #A": "Received", "Value #B": "Sent"}}},
        {"id": "calculateField", "options": {"mode": "reduceRow", "reduce": {"reducer": "sum", "include": ["Received", "Sent"]}, "alias": "Total"}},
        {"id": "sortBy", "options": {"sort": [{"field": "Total", "desc": True}]}},
    ],
    "fieldConfig": {"defaults": {"unit": "bytes", "decimals": 1, "custom": {"align": "auto"}},
                    "overrides": [{"matcher": {"id": "byName", "options": "Interface"}, "properties": [{"id": "unit", "value": "string"}]}]},
    "options": {"showHeader": True, "cellHeight": "sm", "footer": {"show": False}},
}
panels += [
    ts("Throughput per interface (rx up, tx down)",
       [t(f'max by (device) (rate(node_network_receive_bytes_total{{{NODE}}}[$__rate_interval]))', "{{device}} rx"),
        t(f'max by (device) (rate(node_network_transmit_bytes_total{{{NODE}}}[$__rate_interval]))', "{{device}} tx", ref="B")],
       g(0, y, 12, 8), unit="Bps", overrides=[IFACE_TX]),
    table,
]
y += 8
panels.append({
    "type": "state-timeline", "title": "Link state per interface", "id": nid(), "datasource": PROM, "gridPos": g(0, y, 12, 7),
    "description": "Kernel operstate (netclass collector). wwan0 flapping = LTE dropping; tailscale0 down = no SSH.",
    "targets": [t(f'max by (device) (node_network_up{{device=~"eth0|wlan0|wwan0|usb0|tailscale0",{NODE}}})', "{{device}}")],
    "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "thresholds": thresholds((None, "red"), (1, "green")),
                                 "mappings": vmap({"1": ("up", "green"), "0": ("DOWN", "red")}),
                                 "custom": {"fillOpacity": 85, "lineWidth": 0}}, "overrides": []},
    "options": {"showValue": "never", "mergeValues": True, "rowHeight": 0.85, "legend": {"showLegend": False}, "tooltip": {"mode": "single"}}})
panels.append(
    ts("Errors & drops per interface",
       [t(f'sum by (device) (rate(node_network_receive_errs_total{{{NODE}}}[$__rate_interval]) + rate(node_network_transmit_errs_total{{{NODE}}}[$__rate_interval]))', "{{device}} errors"),
        t(f'sum by (device) (rate(node_network_receive_drop_total{{{NODE}}}[$__rate_interval]) + rate(node_network_transmit_drop_total{{{NODE}}}[$__rate_interval]))', "{{device}} drops", ref="B")],
       g(12, y, 12, 7), unit="pps", minv=0, desc="Should be flat zero. Sustained errors on wwan0 = poor signal or USB power trouble on the modem."))
y += 7
panels += [
    ts("LTE daily burn rate", [t(f'{LTE_RATE} * 86400', "MB/day at last hour's rate")], g(0, y, 12, 7), unit="decbytes", minv=0,
       th=thresholds((None, "transparent"), (240e6, "orange"), (480e6, "red")), th_style="line",
       desc="Bytes/day if the last hour's wwan0 rate held. Lines: 10 and 20 MB/hr budget (240 / 480 MB/day)."),
    stat("Days to burn the 500 MB SIM", [t(f'{SIM_CAP_BYTES} / ({LTE_RATE} * 86400)', instant=True)], g(12, y, 6, 7), unit="d", decimals=1,
         th=thresholds((None, "red"), (1, "orange"), (3, "green")), novalue="No LTE modem",
         desc="500 MB ÷ current daily rate: run-time budget for a fresh SIM, not what's left on yours (1NCE portal has that)."),
    stat("LTE used in range", [t(f'sum(increase(node_network_receive_bytes_total{{device="wwan0",{NODE}}}[$__range]) + increase(node_network_transmit_bytes_total{{device="wwan0",{NODE}}}[$__range]))', instant=True)],
         g(18, y, 6, 7), unit="decbytes", decimals=1, th=thresholds((None, "text")), color_mode="value", novalue="No LTE modem"),
]
y += 7
panels.append(
    ts("Sample lag (data freshness)", [t(f'time() - max(timestamp(up{{{NODE}}}))', "lag")], g(0, y, 24, 6), unit="s", minv=0,
       th=thresholds((None, "transparent"), (60, "orange"), (180, "red")), th_style="line+area",
       desc="Age of the newest sample at each point. Saw-tooth up to 15 s is normal; climbs then gaps when LTE drops; holes fill in once the WAL replays."))
y += 6
# ---------------- Logs -----------------------------------------------------
panels.append(row("Logs", y)); y += 1
vol = ts("Log volume by unit & level", [t(f'sum by (unit, level) (count_over_time({{job="systemd-journal",{H},unit=~"$unit",level=~"$level"}} [$__auto]))',
                                            "{{unit}} · {{level}}", ds=LOKI)], g(0, y, 24, 6), stack=True)
vol["fieldConfig"]["defaults"]["custom"].update(drawStyle="bars", fillOpacity=80)
panels.append(vol)
y += 6
panels.append({"type": "logs", "title": "System logs", "id": nid(), "datasource": LOKI, "gridPos": g(0, y, 24, 14),
               "targets": [t(f'{{job="systemd-journal",{H},unit=~"$unit",level=~"$level"}}', ds=LOKI)],
               "options": {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending",
                           "enableLogDetails": True, "prettifyLogMessage": False, "dedupStrategy": "none"}})

loki_var = lambda name, label: {
    "name": name, "label": label, "type": "query", "datasource": LOKI,
    "query": {"type": 1, "label": name, "stream": '{job="systemd-journal"}', "refId": name},
    "definition": f'label_values({{job="systemd-journal"}}, {name})',
    "refresh": 2, "includeAll": True, "multi": True, "allValue": ".*", "current": {"text": "All", "value": "$__all"}, "sort": 1}

dash = {
    "uid": "haav-pi-detail", "title": "Haav Pi", "tags": ["haav", "raspberry-pi"],
    "description": "Raspberry Pi drill-down: power/firmware, CPU, memory, SD card, network/LTE, logs. Source: haav-telemetry/dashboards/pi.json (generated).",
    "timezone": "browser", "refresh": "30s", "schemaVersion": 39, "editable": True, "graphTooltip": 1,
    "time": {"from": "now-6h", "to": "now"},
    "links": [link("Underway", "haav-pi", "arrow-left")],
    "templating": {"list": datasource_vars() + [loki_var("unit", "Log unit"), loki_var("level", "Log level")]},
    "annotations": {"list": [
        {"name": "Pi reboots", "enable": True, "iconColor": "orange", "datasource": PROM,
         "expr": f'changes(node_boot_time_seconds{{{NODE}}}[2m]) > 0', "step": "1m", "titleFormat": "Pi rebooted", "textFormat": "{{host}}"},
    ]},
    "panels": panels,
}
json.dump(dash, open(sys.argv[1], "w"), indent=2)
print(len(panels), "panels")
