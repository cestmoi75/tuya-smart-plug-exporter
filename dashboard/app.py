#!/usr/bin/env python3
"""Small, fixed-query Tuya power dashboard proxy for Prometheus."""
import json
import os
import re
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://127.0.0.1:9090").rstrip("/")
# Device filters are supplied by the dashboard as a safe Prometheus regex.
DEVICE_RE = re.compile(r"^[A-Za-z0-9_.:*\- ]{0,120}$")
DEFAULT_DEVICE = "*"
CACHE_TTL = max(1, int(os.environ.get("CACHE_TTL_SECONDS", "5")))
MAX_HISTORY_HOURS = min(168, max(1, int(os.environ.get("MAX_HISTORY_HOURS", "24"))))
STATIC = Path(__file__).parent / "static"
_cache = {}


def device_regex(query):
    value = query.get("device", [DEFAULT_DEVICE])[0]
    if not isinstance(value, str) or not DEVICE_RE.fullmatch(value):
        raise ValueError("invalid device filter")
    return ".*" if value in ("", "*") else value


def power_query(device=".*"):
    # Device names are escaped before insertion; this endpoint never accepts raw PromQL.
    return f'sum by (device_name) (tuya_consumption_power{{device_name=~"{device}"}})'


def prometheus(query, start=None, end=None, step=None):
    params = {"query": query}
    if start is not None:
        params.update(start=str(start), end=str(end), step=str(step or 60))
    from urllib.parse import urlencode
    req = Request(f"{PROMETHEUS_URL}/api/v1/query{'_range' if start is not None else ''}?{urlencode(params)}")
    with urlopen(req, timeout=4) as response:
        payload = json.load(response)
    if payload.get("status") != "success":
        raise RuntimeError("Prometheus query failed")
    return payload.get("data", {})


def prometheus_label_values(label):
    from urllib.parse import quote
    req = Request(f"{PROMETHEUS_URL}/api/v1/label/{quote(label, safe='')}/values")
    with urlopen(req, timeout=4) as response:
        payload = json.load(response)
    if payload.get("status") != "success":
        raise RuntimeError("Prometheus label query failed")
    return payload.get("data", [])


def cached(key, fn):
    now = time.time()
    item = _cache.get(key)
    if item and now - item[0] < CACHE_TTL:
        return item[1]
    value = fn()
    _cache[key] = (now, value)
    return value


class Handler(BaseHTTPRequestHandler):
    server_version = "TuyaPowerDashboard/1.0"

    def log_message(self, fmt, *args):
        return

    def set_headers(self, content_type="application/json; charset=utf-8"):
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; style-src 'self'; script-src 'self'")
        self.send_header("Referrer-Policy", "no-referrer")

    def reply(self, status, body, content_type="application/json; charset=utf-8"):
        raw = body if isinstance(body, bytes) else body.encode()
        self.send_response(status)
        self.set_headers(content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            return self.reply(HTTPStatus.OK, json.dumps({"status": "ok"}))
        if parsed.path == "/api/power/current":
            return self.current(parse_qs(parsed.query))
        if parsed.path == "/api/power/history":
            return self.history(parse_qs(parsed.query))
        if parsed.path == "/api/devices":
            return self.devices()
        if parsed.path == "/":
            return self.static("index.html", "text/html; charset=utf-8")
        if parsed.path.startswith("/static/"):
            name = parsed.path.removeprefix("/static/")
            if "/" not in name and name in {"app.js", "style.css"}:
                return self.static(name, "text/javascript; charset=utf-8" if name.endswith(".js") else "text/css; charset=utf-8")
        return self.reply(HTTPStatus.NOT_FOUND, json.dumps({"error": "not found"}))

    def static(self, name, content_type):
        try:
            raw = (STATIC / name).read_bytes()
        except OSError:
            return self.reply(HTTPStatus.NOT_FOUND, b"not found", "text/plain; charset=utf-8")
        return self.reply(HTTPStatus.OK, raw, content_type)

    def current(self, query):
        try:
            device = device_regex(query)
            prom_query = power_query(device)
            data = cached(f"current:{device}", lambda: prometheus(prom_query))
            readings = [
                {"device_name": item.get("metric", {}).get("device_name", "unknown"),
                 "power_w": float(item["value"][1])}
                for item in data.get("result", [])
            ]
            return self.reply(HTTPStatus.OK, json.dumps({
                "readings": readings,
                "power_w": sum(item["power_w"] for item in readings),
                "query": prom_query,
                "updated_at": time.time(),
            }))
        except ValueError as exc:
            return self.reply(HTTPStatus.BAD_REQUEST, json.dumps({"error": str(exc)}))
        except Exception as exc:
            return self.reply(HTTPStatus.BAD_GATEWAY, json.dumps({"error": "upstream unavailable", "detail": str(exc)}))

    def devices(self):
        try:
            values = cached("devices", lambda: prometheus_label_values("device_name"))
            return self.reply(HTTPStatus.OK, json.dumps({"devices": sorted(values)}))
        except Exception as exc:
            # The dashboard remains usable with the all-devices selector if labels are unavailable.
            return self.reply(HTTPStatus.OK, json.dumps({"devices": [], "warning": str(exc)}))

    def history(self, query):
        try:
            hours = float(query.get("hours", [MAX_HISTORY_HOURS])[0])
            hours = min(MAX_HISTORY_HOURS, max(1, hours))
        except (TypeError, ValueError):
            hours = MAX_HISTORY_HOURS
        end = time.time()
        start = end - hours * 3600
        step = max(30, int(hours * 3600 / 240))
        try:
            device = device_regex(query)
            prom_query = power_query(device)
            data = cached(f"history:{hours}:{device}", lambda: prometheus(prom_query, start, end, step))
            series = []
            for item in data.get("result", []):
                series.append({"metric": item.get("metric", {}), "values": item.get("values", [])})
            return self.reply(HTTPStatus.OK, json.dumps({"series": series, "from": start, "to": end, "step": step, "query": prom_query}))
        except ValueError as exc:
            return self.reply(HTTPStatus.BAD_REQUEST, json.dumps({"error": str(exc)}))
        except Exception as exc:
            return self.reply(HTTPStatus.BAD_GATEWAY, json.dumps({"error": "upstream unavailable", "detail": str(exc)}))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8088"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
