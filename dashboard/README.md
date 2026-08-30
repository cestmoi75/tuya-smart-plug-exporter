# Tuya Power Dashboard

A small, dependency-free web dashboard for the exporter metrics stored in Prometheus.

## Run

From this directory:

```bash
PROMETHEUS_URL=http://192.168.0.124:9090 PORT=8088 python3 app.py
```

Then open `http://<host>:8088/`.

## Synology Container Manager

Copy this `dashboard` directory to the NAS, for example `/volume1/docker/tuya-power-dashboard`, then run from that directory:

```bash
docker compose up -d --build
curl http://127.0.0.1:8088/healthz
```

Container Manager can also import the included `compose.yaml`. Port `8088` must be allowed in DSM's firewall if the dashboard is accessed from another LAN device.

Environment variables:

- `PROMETHEUS_URL`: Prometheus base URL (default `http://192.168.0.124:9090`)
- `PORT`: listen port (default `8088`)
- `CACHE_TTL_SECONDS`: upstream cache duration (default `5`)
- `MAX_HISTORY_HOURS`: maximum requested history (default `24`, capped at `168`)

The API only accepts a restricted device-name filter and generates fixed PromQL; it does not proxy arbitrary queries.
