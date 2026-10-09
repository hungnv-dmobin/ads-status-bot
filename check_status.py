#!/usr/bin/env python3
"""Poll the Google Ads publisher status dashboard and relay new incidents/updates to Discord.

State (seen incidents + last update timestamp) is kept in state.json so each
event is announced exactly once. On the very first run the current feed is
recorded silently to avoid flooding the channel with historical incidents.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

FEED_URL = "https://ads.google.com/status/publisher/incidents.json"
INCIDENT_URL = "https://ads.google.com/status/publisher/{uri}"
STATE_FILE = Path(__file__).with_name("state.json")
USER_AGENT = "ads-status-bot/1.0 (+github actions)"

# Product ids from https://ads.google.com/status/publisher/products.json
WATCHED_PRODUCTS = {
    "dcPJkdGcHpXEwoueeXcd": "AdMob",
    "jfmvrbQcKd2YLioeQbVF": "Google Ad Manager",
    "69xSskskydaxQTsgRrNP": "Google Ad Manager DAI",
    "SucNwZJBfBS591N49trV": "Google Ad Manager Data Transfer",
    "8JMV6XLjvGegH3qzSnqL": "AdSense",
}

STATUS_STYLE = {
    "SERVICE_OUTAGE": ("🔴", "Outage", 0xD93025),
    "SERVICE_DISRUPTION": ("🟠", "Disruption", 0xF29900),
    "SERVICE_INFORMATION": ("🔵", "Information", 0x1A73E8),
    "AVAILABLE": ("✅", "Resolved", 0x188038),
}

# Touch state.json at least this often so the repo never looks inactive
# (GitHub disables scheduled workflows after 60 days without activity).
HEARTBEAT_EVERY = timedelta(days=30)

EMBED_DESC_LIMIT = 4000


def http_json(url: str) -> object:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def post_discord(webhook: str, payload: dict) -> None:
    body = json.dumps(payload).encode()
    for attempt in range(5):
        req = urllib.request.Request(
            webhook,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30):
                return
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 4:
                retry_after = float(json.load(e).get("retry_after", 1))
                time.sleep(retry_after + 0.5)
                continue
            raise RuntimeError(f"Discord webhook failed: HTTP {e.code} {e.read()[:300]!r}") from e


def load_state() -> dict | None:
    if not STATE_FILE.exists():
        return None
    return json.loads(STATE_FILE.read_text())


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def watched_names(incident: dict) -> list[str]:
    return [
        WATCHED_PRODUCTS[p["id"]]
        for p in incident.get("affected_products", [])
        if p["id"] in WATCHED_PRODUCTS
    ]


def truncate(text: str, limit: int = EMBED_DESC_LIMIT) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_embed(incident: dict, update: dict, is_new: bool) -> dict:
    emoji, label, color = STATUS_STYLE.get(update["status"], ("⚪", update["status"], 0x5F6368))
    products = ", ".join(watched_names(incident))
    kind = "New incident" if is_new else ("Resolved" if update["status"] == "AVAILABLE" else "Update")
    fields = [
        {"name": "Product", "value": products or "-", "inline": True},
        {"name": "Status", "value": f"{emoji} {label}", "inline": True},
        {"name": "Severity", "value": incident.get("severity", "-"), "inline": True},
        {"name": "Began", "value": f"<t:{_epoch(incident['begin'])}:f>", "inline": True},
    ]
    if incident.get("end"):
        fields.append({"name": "Ended", "value": f"<t:{_epoch(incident['end'])}:f>", "inline": True})
    return {
        "title": truncate(f"{emoji} [{kind}] {products}", 256),
        "url": INCIDENT_URL.format(uri=incident["uri"]),
        "description": truncate(update["text"]),
        "color": color,
        "fields": fields,
        "timestamp": update["when"],
        "footer": {"text": f"Incident {incident['id']}"},
    }


def _epoch(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp())


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook and not dry_run:
        print("DISCORD_WEBHOOK_URL is not set", file=sys.stderr)
        return 2

    incidents = [i for i in http_json(FEED_URL) if watched_names(i)]
    now = datetime.now(timezone.utc)
    state = load_state()

    if state is None and dry_run:
        state = {"incidents": {}}
    elif state is None:
        state = {
            "incidents": {i["id"]: i["updates"][0]["when"] for i in incidents if i["updates"]},
            "heartbeat": now.isoformat(timespec="seconds"),
        }
        save_state(state)
        print(f"First run: recorded {len(state['incidents'])} existing incidents without notifying")
        return 0

    seen: dict = state["incidents"]
    events = []  # (incident, update, is_new) ordered oldest first
    for incident in incidents:
        updates = sorted(incident["updates"], key=lambda u: u["when"])
        if not updates:
            continue
        last_seen = seen.get(incident["id"])
        if last_seen is None:
            # New incident: announce with its latest update only.
            events.append((incident, updates[-1], True))
        else:
            events.extend((incident, u, False) for u in updates if u["when"] > last_seen)
    events.sort(key=lambda e: e[1]["when"])

    failed = False
    for incident, update, is_new in events:
        embed = build_embed(incident, update, is_new)
        print(f"Notify: {embed['title']} ({update['when']})")
        if not dry_run:
            try:
                post_discord(webhook, {"username": "Google Ads Status", "embeds": [embed]})
            except RuntimeError as e:
                # Stop here; unsent events are retried on the next run.
                print(e, file=sys.stderr)
                failed = True
                break
            time.sleep(1)
        seen[incident["id"]] = max(seen.get(incident["id"], ""), update["when"])

    # Forget incidents that have dropped out of the feed.
    feed_ids = {i["id"] for i in incidents}
    state["incidents"] = {k: v for k, v in seen.items() if k in feed_ids}

    if now - datetime.fromisoformat(state.get("heartbeat", "1970-01-01T00:00:00+00:00")) > HEARTBEAT_EVERY:
        state["heartbeat"] = now.isoformat(timespec="seconds")

    if not dry_run:
        save_state(state)
    print(f"Checked {len(incidents)} watched incidents, {len(events)} event(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
