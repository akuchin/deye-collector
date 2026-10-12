"""Poll a Deye inverter via its Solarman Wi-Fi logger and push metrics to Grafana Cloud."""

import base64
import logging
import os
import signal
import threading
import time
import urllib.error
import urllib.request

from pysolarmanv5 import PySolarmanV5

from registers import BLOCKS, decode

log = logging.getLogger("deye")


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise SystemExit(f"Missing required env var {name}")
    return value


class Inverter:
    def __init__(self, ip: str, serial: int, port: int, slave_id: int):
        self._args = dict(address=ip, serial=serial, port=port, mb_slave_id=slave_id, socket_timeout=10)
        self._client: PySolarmanV5 | None = None

    def read(self) -> dict[str, float]:
        try:
            if self._client is None:
                self._client = PySolarmanV5(**self._args)
            raw: dict[int, int] = {}
            for start, count in BLOCKS:
                for i, value in enumerate(self._client.read_holding_registers(start, count)):
                    raw[start + i] = value
            return decode(raw)
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.disconnect()
            except Exception:
                pass
            self._client = None


class GrafanaPusher:
    """Pushes InfluxDB line protocol to Grafana Cloud; prints it when not configured (dry run)."""

    def __init__(self, url: str, user: str, token: str, tags: dict[str, str]):
        self.url = url
        self.dry_run = not (url and user and token)
        self._auth = "Basic " + base64.b64encode(f"{user}:{token}".encode()).decode()
        self._tags = "".join(f",{k}={v.replace(' ', '_')}" for k, v in tags.items())

    def push(self, fields: dict[str, float]) -> None:
        body = ",".join(f"{k}={v}" for k, v in fields.items())
        line = f"deye{self._tags} {body} {time.time_ns()}"
        if self.dry_run:
            print(line, flush=True)
            return
        req = urllib.request.Request(
            self.url,
            data=line.encode(),
            headers={"Authorization": self._auth, "Content-Type": "text/plain"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15):
                pass
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace").strip()[:300]
            raise RuntimeError(f"HTTP {e.code}: {detail}") from None


def main() -> None:
    logging.basicConfig(
        level=env("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    inverter = Inverter(
        ip=env("DEYE_LOGGER_IP"),
        serial=int(env("DEYE_LOGGER_SERIAL")),
        port=int(env("DEYE_LOGGER_PORT", "8899")),
        slave_id=int(env("DEYE_SLAVE_ID", "1")),
    )
    pusher = GrafanaPusher(
        url=env("GRAFANA_INFLUX_URL", ""),
        user=env("GRAFANA_USER", ""),
        token=env("GRAFANA_TOKEN", ""),
        tags={"site": env("SITE", "home")},
    )
    interval = float(env("POLL_INTERVAL", "30"))
    if pusher.dry_run:
        log.warning("Grafana is not configured: dry run, metrics are printed to stdout")
    else:
        log.info("Pushing to %s", pusher.url)

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    while not stop.is_set():
        started = time.monotonic()
        try:
            fields = {"up": 1.0, **inverter.read()}
        except Exception as e:
            log.error("Inverter read failed: %s", e)
            fields = {"up": 0.0}
        else:
            log.info(
                "SOC=%s%% battery=%sW load=%sW grid_up=%s",
                fields["battery_soc_percent"], fields["battery_power_w"],
                fields["load_power_w"], int(fields["grid_up"]),
            )
        try:
            pusher.push(fields)
        except Exception as e:
            log.error("Push failed: %s", e)
        stop.wait(max(0.0, interval - (time.monotonic() - started)))

    inverter.close()
    log.info("Stopped")


if __name__ == "__main__":
    main()
