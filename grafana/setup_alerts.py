"""Create or update Deye battery alerts in Grafana Cloud with Telegram notifications.

Idempotent: safe to run again after changing thresholds or the message template.
Required env: GRAFANA_URL, GRAFANA_SA_TOKEN, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.
Optional env: GRAFANA_DATASOURCE_UID (default: the "grafanacloud-*-prom" datasource).
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request

FOLDER_UID = "deye"
FOLDER_TITLE = "Deye"
RULE_GROUP = "battery"
CONTACT_POINT = "Deye Telegram"
TEMPLATE_NAME = "deye"
ROUTE_MATCHER = ["deye", "=", "battery"]

# (fire below this SOC %, severity, rule title, pending period).
# Every level recovers only when SOC is above RECOVER_ABOVE.
LEVELS = [
    # Longer pending period: SOC can briefly dip to 99% while the battery floats on grid.
    (100, "discharge", "Работаем на батарее", "5m"),
    (75, "info", "Батарея ниже 75%", "1m"),
    (50, "warning", "Батарея ниже 50%", "1m"),
    (25, "critical", "Батарея ниже 25%", "1m"),
]
RECOVER_ABOVE = 99

MESSAGE_TEMPLATE = """{{ define "deye.message" -}}
{{- if .Alerts.Firing -}}
{{- $top := index .Alerts.Firing 0 -}}
{{- range .Alerts.Firing -}}
{{- if eq .Labels.severity "critical" }}{{ $top = . }}
{{- else if and (eq .Labels.severity "warning") (ne $top.Labels.severity "critical") }}{{ $top = . }}
{{- else if and (eq .Labels.severity "info") (eq $top.Labels.severity "discharge") }}{{ $top = . }}{{ end -}}
{{- end -}}
{{- if eq $top.Labels.severity "discharge" -}}
🔋 Работаем на батарее <b>{{ $top.Annotations.soc }}%</b> · {{ $top.Labels.site }}
{{- else -}}
{{- if eq $top.Labels.severity "critical" }}🔴 <b>CRITICAL</b>{{ else if eq $top.Labels.severity "warning" }}🟠 <b>WARNING</b>{{ else }}🔵 <b>INFO</b>{{ end }} · {{ $top.Labels.site }}
Заряд батареи: <b>{{ $top.Annotations.soc }}%</b> (ниже {{ $top.Annotations.threshold }}%)
{{- end -}}
{{- else -}}
✅ Батарея заряжена на <b>100%</b> · {{ .CommonLabels.site }}
{{- end }}
{{- end }}"""


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if not value:
        raise SystemExit(f"Missing required env var {name}")
    return value


BASE_URL = env("GRAFANA_URL").rstrip("/")
TOKEN = env("GRAFANA_SA_TOKEN")


def api(method: str, path: str, body=None, ok_missing: bool = False):
    req = urllib.request.Request(
        BASE_URL + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
            # Keep provisioned resources editable in the Grafana UI.
            "X-Disable-Provenance": "true",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        if ok_missing and e.code == 404:
            return None
        raise SystemExit(f"{method} {path} -> HTTP {e.code}: {e.read().decode(errors='replace')[:500]}")


def find_datasource_uid() -> str:
    if uid := os.environ.get("GRAFANA_DATASOURCE_UID"):
        return uid
    candidates = [
        ds for ds in api("GET", "/api/datasources")
        if ds["type"] == "prometheus" and ds["name"].startswith("grafanacloud-") and ds["name"].endswith("-prom")
    ]
    if len(candidates) != 1:
        names = [ds["name"] for ds in candidates]
        raise SystemExit(f"Cannot pick the Prometheus datasource {names}; set GRAFANA_DATASOURCE_UID")
    print(f"datasource: {candidates[0]['name']} ({candidates[0]['uid']})")
    return candidates[0]["uid"]


def ensure_folder() -> None:
    if api("GET", f"/api/folders/{FOLDER_UID}", ok_missing=True) is None:
        api("POST", "/api/folders", {"uid": FOLDER_UID, "title": FOLDER_TITLE})
    print(f"folder: {FOLDER_TITLE}")


def ensure_template() -> None:
    existing = api("GET", f"/api/v1/provisioning/templates/{TEMPLATE_NAME}", ok_missing=True)
    body = {"template": MESSAGE_TEMPLATE}
    if existing and existing.get("version"):
        body["version"] = existing["version"]
    api("PUT", f"/api/v1/provisioning/templates/{TEMPLATE_NAME}", body)
    print(f"template: {TEMPLATE_NAME}")


def ensure_contact_point() -> None:
    body = {
        "name": CONTACT_POINT,
        "type": "telegram",
        "disableResolveMessage": False,
        "settings": {
            "bottoken": env("TELEGRAM_BOT_TOKEN"),
            "chatid": env("TELEGRAM_CHAT_ID"),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "message": '{{ template "deye.message" . }}',
        },
    }
    query = urllib.parse.urlencode({"name": CONTACT_POINT})
    existing = api("GET", f"/api/v1/provisioning/contact-points?{query}") or []
    if existing:
        api("PUT", f"/api/v1/provisioning/contact-points/{existing[0]['uid']}", {**body, "uid": existing[0]["uid"]})
    else:
        api("POST", "/api/v1/provisioning/contact-points", {**body, "uid": "deye-telegram"})
    print(f"contact point: {CONTACT_POINT}")


def ensure_policy() -> None:
    """Add (or replace) one child route; all other policies stay as they are."""
    tree = api("GET", "/api/v1/provisioning/policies")
    route = {
        "receiver": CONTACT_POINT,
        "object_matchers": [ROUTE_MATCHER],
        # One group for all levels: escalations update it, full recovery sends a single "All OK".
        "group_by": ["deye", "site"],
        "group_wait": "30s",
        "group_interval": "1m",
        "repeat_interval": "6h",
    }
    routes = [r for r in tree.get("routes") or [] if r.get("object_matchers") != [ROUTE_MATCHER]]
    tree["routes"] = [route, *routes]
    api("PUT", "/api/v1/provisioning/policies", tree)
    print("notification policy: deye=battery -> " + CONTACT_POINT)


def rule(ds_uid: str, threshold: int, severity: str, title: str, pending: str) -> dict:
    return {
        "uid": f"deye-soc-{threshold}",
        "title": title,
        "folderUID": FOLDER_UID,
        "ruleGroup": RULE_GROUP,
        "condition": "C",
        "for": pending,
        "noDataState": "KeepLast",
        "execErrState": "KeepLast",
        "labels": {"deye": "battery", "severity": severity},
        "annotations": {
            "summary": f"SOC ниже {threshold}%, восстановление при 100%",
            "soc": '{{ printf "%.0f" $values.B.Value }}',
            "threshold": str(threshold),
        },
        "data": [
            {
                "refId": "A",
                "datasourceUid": ds_uid,
                "relativeTimeRange": {"from": 600, "to": 0},
                "model": {"refId": "A", "expr": "deye_battery_soc_percent", "instant": True, "range": False},
            },
            {
                "refId": "B",
                "datasourceUid": "__expr__",
                "model": {"refId": "B", "type": "reduce", "expression": "A", "reducer": "last"},
            },
            {
                "refId": "C",
                "datasourceUid": "__expr__",
                "model": {
                    "refId": "C",
                    "type": "threshold",
                    "expression": "B",
                    "conditions": [{
                        "evaluator": {"type": "lt", "params": [threshold]},
                        "unloadEvaluator": {"type": "gt", "params": [RECOVER_ABOVE]},
                    }],
                },
            },
        ],
    }


def ensure_rules(ds_uid: str) -> None:
    group = {
        "title": RULE_GROUP,
        "folderUid": FOLDER_UID,
        "interval": 60,
        "rules": [rule(ds_uid, *level) for level in LEVELS],
    }
    api("PUT", f"/api/v1/provisioning/folder/{FOLDER_UID}/rule-groups/{RULE_GROUP}", group)
    for t, s, title, pending in LEVELS:
        print(f"rule: {title}: SOC < {t}% for {pending} [{s}], recovers at > {RECOVER_ABOVE}%")


def main() -> None:
    ds_uid = find_datasource_uid()
    ensure_folder()
    ensure_template()
    ensure_contact_point()
    ensure_policy()
    ensure_rules(ds_uid)
    print("done")


if __name__ == "__main__":
    main()
