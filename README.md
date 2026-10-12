# deye-collector

This collector reads metrics from a Deye single-phase hybrid inverter through its Solarman Wi-Fi logger (TCP port 8899, Solarman V5 protocol).
It sends the metrics to Grafana Cloud in InfluxDB line protocol. Grafana Cloud stores them as Prometheus metrics.

## Configure

```sh
cp .env.example .env   # then set the GRAFANA_* values
```

If the `GRAFANA_*` values are empty, the collector runs in dry-run mode. In this mode, it prints the metrics to stdout.

Get the Grafana values from **Grafana Cloud Portal → your stack → Prometheus → Details**:

- `GRAFANA_INFLUX_URL`: use the Prometheus host, change `prometheus-` to `influx-`, and set the path to `/api/v1/push/influx/write`.
  Example: `https://influx-prod-24-prod-eu-west-2.grafana.net/api/v1/push/influx/write`.
- `GRAFANA_USER`: the numeric user / instance ID.
- `GRAFANA_TOKEN`: an access policy token with the `metrics:write` scope.

## Run

Local:

```sh
uv run --env-file .env python collector.py
```

Docker (for example, on a Raspberry Pi):

```sh
docker compose up -d --build
docker compose logs -f
```

Docker Hub image (no source code needed on the target device, only `docker-compose.yml` and `.env`):

```sh
docker compose pull && docker compose up -d
```

Run the collector on a device that is on the inverter's backup output. If you do not, the metrics stop during a power outage.

## Publish the image

### CI (GitHub Actions)

The workflow `.github/workflows/docker.yml` builds the image for amd64 and arm64 and pushes it to Docker Hub:

- Push to `main`: tags `latest` and `sha-<short>`.
- Git tag `v1.2.3`: tags `1.2.3` and `1.2`.
- Pull request: build only, no push.

Add these repository secrets (Settings → Secrets and variables → Actions):

- `DOCKERHUB_USERNAME`: `temak`
- `DOCKERHUB_TOKEN`: a Docker Hub access token with the Read & Write scope (Docker Hub → Account settings → Personal access tokens).

### Manual

Build for amd64 and arm64 (Raspberry Pi), then push to Docker Hub:

```sh
docker login
docker buildx build --platform linux/amd64,linux/arm64 \
  -t temak/deye-collector:0.1.0 -t temak/deye-collector:latest --push .
```

## Metrics

All metrics have the `deye_` prefix and the `site` label. Examples:

| Metric | Meaning |
|---|---|
| `deye_up` | 1 = the inverter read was successful, 0 = the read failed |
| `deye_battery_soc_percent` | Battery state of charge |
| `deye_battery_power_w` | Battery power. Positive = discharge, negative = charge |
| `deye_grid_up` | 1 = grid voltage ≥ 180 V |
| `deye_grid_voltage_v`, `deye_grid_power_w` | Grid voltage and grid power (positive = import) |
| `deye_load_power_w` | Load power |
| `deye_pv_power_w` | Solar power (PV1 + PV2) |
| `deye_*_today_kwh` | Daily energy counters |

For the full list, see `registers.py`.

## Alert ideas (Grafana Alerting)

| Alert | Query | Pending period |
|---|---|---|
| Low battery | `deye_battery_soc_percent < 20` | 2m |
| Grid down | `deye_grid_up == 0` | 1m |
| Grid restored | `deye_grid_up == 1` (or use the "resolved" notification of "Grid down") | 1m |
| Inverter unreachable | `deye_up == 0` | 5m |
| No data (collector or internet down) | `absent_over_time(deye_up[5m])` | 0m |
