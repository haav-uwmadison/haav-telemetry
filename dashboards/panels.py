"""Shared panel builders for the Haav Grafana dashboards (build_*.py)."""
PROM = {"type": "prometheus", "uid": "${prom}"}
LOKI = {"type": "loki", "uid": "${loki}"}
H = 'host=~"$host"'
NODE = f'job=~"integrations/(unix|raspberrypi-node)",{H}'
pid = [0]
def nid():
    pid[0] += 1; return pid[0]

def t(expr, legend="", instant=False, ref="A", ds=PROM):
    d = {"datasource": ds, "expr": expr, "legendFormat": legend, "refId": ref}
    if instant: d.update(instant=True, range=False)
    return d

def row(title, y):
    return {"type": "row", "title": title, "id": nid(), "collapsed": False, "panels": [],
            "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}}

def thresholds(*steps):
    return {"mode": "absolute", "steps": [{"color": c, "value": v} for v, c in steps]}

def stat(title, targets, pos, unit="none", th=None, mappings=None, desc="", decimals=None, novalue=None, color_mode="background"):
    th = th or thresholds((None, "green"))
    # No data -> neutral grey instead of the lowest threshold's colour (red/green would lie).
    th = {"mode": "absolute", "steps": [{"color": "#5a5d66", "value": None}, {"color": th["steps"][0]["color"], "value": -1e15}] + th["steps"][1:]}
    defaults = {"unit": unit, "thresholds": th, "mappings": mappings or [],
                "color": {"mode": "thresholds"}}
    if decimals is not None: defaults["decimals"] = decimals
    if novalue: defaults["noValue"] = novalue
    return {"type": "stat", "title": title, "id": nid(), "description": desc, "datasource": targets[0]["datasource"],
            "gridPos": pos, "targets": targets,
            "fieldConfig": {"defaults": defaults, "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "colorMode": color_mode, "graphMode": "none", "justifyMode": "center",
                        "textMode": "auto", "orientation": "auto", "wideLayout": True}}

def ts(title, targets, pos, unit="none", desc="", th=None, overrides=None, minv=None, maxv=None, stack=False, th_style="off", legend_calcs=None):
    custom = {"drawStyle": "line", "lineWidth": 1, "fillOpacity": 10, "showPoints": "never",
              "spanNulls": False, "thresholdsStyle": {"mode": th_style},
              "stacking": {"mode": "normal" if stack else "none", "group": "A"}}
    defaults = {"unit": unit, "custom": custom, "color": {"mode": "palette-classic"},
                "thresholds": th or thresholds((None, "green"))}
    if minv is not None: defaults["min"] = minv
    if maxv is not None: defaults["max"] = maxv
    return {"type": "timeseries", "title": title, "id": nid(), "description": desc, "datasource": targets[0]["datasource"],
            "gridPos": pos, "targets": targets,
            "fieldConfig": {"defaults": defaults, "overrides": overrides or []},
            "options": {"legend": {"displayMode": "table" if legend_calcs else "list", "placement": "bottom",
                                   "calcs": legend_calcs or []},
                        "tooltip": {"mode": "multi", "sort": "desc"}}}

def g(x, y, w, h): return {"x": x, "y": y, "w": w, "h": h}

MAV = H
def vmap(d):  # {"0": ("text","color"), ...}
    return [{"type": "value", "options": {k: {"text": tx, "color": c, "index": i} for i, (k, (tx, c)) in enumerate(d.items())}}]
def rmap(lo, hi, tx, c):
    return {"type": "range", "options": {"from": lo, "to": hi, "result": {"text": tx, "color": c, "index": 9}}}
def collapsed(title, y, children):
    r = row(title, y); r["collapsed"] = True; r["panels"] = children; return r


def datasource_vars():
    return [
        {"name": "prom", "label": "Metrics", "type": "datasource", "query": "prometheus", "regex": "/-prom$/", "current": {"text": "grafanacloud-prom", "value": "grafanacloud-prom"}, "hide": 2},
        {"name": "loki", "label": "Logs", "type": "datasource", "query": "loki", "regex": "/-logs$/", "current": {"text": "grafanacloud-logs", "value": "grafanacloud-logs"}, "hide": 2},
        {"name": "host", "label": "Host", "type": "query", "datasource": PROM,
         "query": {"query": "label_values(node_boot_time_seconds, host)", "refId": "host"},
         "definition": "label_values(node_boot_time_seconds, host)", "refresh": 2, "includeAll": True, "multi": True,
         "current": {"text": "All", "value": "$__all"}, "sort": 1},
    ]


def link(title, uid, icon="dashboard"):
    """Dashboard link that keeps the time range and host selection."""
    return {"title": title, "type": "link", "url": f"/d/{uid}", "icon": icon, "keepTime": True, "includeVars": True,
            "targetBlank": False, "asDropdown": False, "tags": [], "tooltip": ""}
