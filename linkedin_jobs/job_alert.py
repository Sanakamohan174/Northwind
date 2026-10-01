"""Check LinkedIn for new jobs and send them to WhatsApp.

Runs hourly from .github/workflows/linkedin-job-alert.yml. Jobs already checked are
remembered in state/seen_jobs.json so each job is sent only once.

WhatsApp is sent via one of (set as environment variables / GitHub secrets):
  CallMeBot (free):  CALLMEBOT_PHONE, CALLMEBOT_APIKEY
  Twilio:            TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM, WHATSAPP_TO

Use --dry-run to print matches without sending or saving state.
"""

import argparse
import json
import os
import random
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

from scrape_linkedin_jobs import add_details, search

STATE_FILE = Path(__file__).parent / "state" / "seen_jobs.json"
KEEP_DAYS = 30
MAX_MESSAGE_CHARS = 1400


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(seen):
    cutoff = (date.today() - timedelta(days=KEEP_DAYS)).isoformat()
    seen = {k: v for k, v in seen.items() if v >= cutoff}
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(seen, indent=1, sort_keys=True) + "\n")


def format_job(job):
    lines = [f"*{job['title']}*", job["company"]]
    extra = " | ".join(x for x in (job.get("seniority_level"), job.get("posted_ago")) if x)
    if extra:
        lines.append(extra)
    lines.append(job["url"])
    return "\n".join(lines)


def build_messages(jobs, header):
    messages, current = [], header
    for job in jobs:
        block = format_job(job)
        if len(current) + len(block) + 2 > MAX_MESSAGE_CHARS:
            messages.append(current)
            current = block
        else:
            current += "\n\n" + block
    messages.append(current)
    return messages


def send_whatsapp(text):
    env = os.environ
    if env.get("CALLMEBOT_PHONE") and env.get("CALLMEBOT_APIKEY"):
        resp = requests.get(
            "https://api.callmebot.com/whatsapp.php",
            params={"phone": env["CALLMEBOT_PHONE"], "text": text, "apikey": env["CALLMEBOT_APIKEY"]},
            timeout=60,
        )
        if resp.status_code != 200 or "ERROR" in resp.text.upper():
            raise RuntimeError(f"CallMeBot failed ({resp.status_code}): {resp.text[:300]}")
        time.sleep(3)  # CallMeBot rate-limits rapid messages
    elif env.get("TWILIO_ACCOUNT_SID") and env.get("TWILIO_AUTH_TOKEN"):
        sid = env["TWILIO_ACCOUNT_SID"]
        resp = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
            auth=(sid, env["TWILIO_AUTH_TOKEN"]),
            data={
                "From": f"whatsapp:{env.get('TWILIO_WHATSAPP_FROM', '+14155238886')}",
                "To": f"whatsapp:{env['WHATSAPP_TO']}",
                "Body": text,
            },
            timeout=60,
        )
        if resp.status_code >= 300:
            raise RuntimeError(f"Twilio failed ({resp.status_code}): {resp.text[:300]}")
    else:
        raise RuntimeError("No WhatsApp sender configured (set CALLMEBOT_* or TWILIO_* secrets)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keywords", default="java selenium")
    parser.add_argument("--location", default="Hyderabad, Telangana, India")
    parser.add_argument("--posted-within", default="r86400", help="LinkedIn f_TPR filter (default 24h)")
    parser.add_argument("--max-jobs", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    seen = load_state()
    session = requests.Session()
    jobs = search(session, args.keywords, args.location, args.max_jobs, args.posted_within)
    new_jobs = [j for j in jobs if j["job_id"] not in seen]
    print(f"{len(jobs)} jobs found, {len(new_jobs)} not seen before")

    matches = []
    for job in new_jobs:
        add_details(session, job)
        if job["mentions_java"] and job["mentions_selenium"]:
            matches.append(job)
        time.sleep(random.uniform(1.0, 2.5))
    print(f"{len(matches)} new jobs mention both Java and Selenium")

    if args.dry_run:
        if matches:
            print("\n---\n".join(build_messages(matches, f"🔔 {len(matches)} new job(s)")))
        return

    today = date.today().isoformat()
    match_ids = {j["job_id"] for j in matches}
    # Non-matching jobs are marked seen right away so their details aren't refetched.
    for job in new_jobs:
        if job["job_id"] not in match_ids:
            seen[job["job_id"]] = today

    if matches:
        header = f"🔔 {len(matches)} new Java + Selenium job(s) in {args.location.split(',')[0]}"
        try:
            for msg in build_messages(matches, header):
                send_whatsapp(msg)
        except RuntimeError as exc:
            save_state(seen)  # matches stay unseen so they're retried next run
            print(exc, file=sys.stderr)
            sys.exit(1)
        for job in matches:
            seen[job["job_id"]] = today

    save_state(seen)


if __name__ == "__main__":
    main()
