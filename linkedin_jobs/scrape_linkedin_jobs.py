"""Scrape LinkedIn job listings via the public (logged-out) guest jobs endpoints.

Usage:
    python scrape_linkedin_jobs.py --keywords "java selenium" --location "Hyderabad, Telangana, India"

Writes CSV and JSON files to the output directory. No LinkedIn login is used.
"""

import argparse
import csv
import json
import random
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
PAGE_SIZE = 10


def text(node):
    return node.get_text(" ", strip=True) if node else ""


def get(session, url, params=None, retries=4):
    for attempt in range(retries):
        resp = session.get(url, params=params, headers=HEADERS, timeout=30)
        if resp.status_code == 200:
            return resp.text
        if resp.status_code in (429, 500, 502, 503, 999):
            time.sleep(2 ** (attempt + 1))
            continue
        if resp.status_code in (400, 404):
            return None
        resp.raise_for_status()
    return None


def parse_cards(html):
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    for card in soup.select("div.base-search-card"):
        urn = card.get("data-entity-urn", "")
        link = card.select_one("a.base-card__full-link")
        posted = card.select_one("time")
        jobs.append(
            {
                "job_id": urn.rsplit(":", 1)[-1],
                "title": text(card.select_one(".base-search-card__title")),
                "company": text(card.select_one(".base-search-card__subtitle")),
                "location": text(card.select_one(".job-search-card__location")),
                "posted_date": posted.get("datetime", "") if posted else "",
                "posted_ago": text(posted),
                "url": link["href"].split("?")[0] if link else "",
            }
        )
    return jobs


def parse_detail(html):
    soup = BeautifulSoup(html, "html.parser")
    detail = {
        "description": text(soup.select_one(".show-more-less-html__markup")),
        "applicants": text(soup.select_one(".num-applicants__caption")),
    }
    for item in soup.select(".description__job-criteria-item"):
        key = text(item.select_one(".description__job-criteria-subheader"))
        value = text(item.select_one(".description__job-criteria-text"))
        if key:
            detail[key.lower().replace(" ", "_")] = value
    return detail


def scrape(keywords, location, max_jobs, with_details, posted_within):
    session = requests.Session()
    jobs, seen = [], set()
    start = 0
    while len(jobs) < max_jobs:
        params = {"keywords": keywords, "location": location, "start": start}
        if posted_within:
            params["f_TPR"] = posted_within
        html = get(session, SEARCH_URL, params)
        cards = parse_cards(html) if html else []
        if not cards:
            break
        for job in cards:
            if job["job_id"] and job["job_id"] not in seen:
                seen.add(job["job_id"])
                jobs.append(job)
        print(f"page start={start}: {len(cards)} cards, {len(jobs)} unique total")
        start += PAGE_SIZE
        time.sleep(random.uniform(1.0, 2.5))
    jobs = jobs[:max_jobs]

    if with_details:
        for i, job in enumerate(jobs, 1):
            html = get(session, DETAIL_URL.format(job_id=job["job_id"]))
            if html:
                job.update(parse_detail(html))
            desc = job.get("description", "")
            job["mentions_java"] = bool(re.search(r"\bjava\b", desc, re.I))
            job["mentions_selenium"] = bool(re.search(r"selenium", desc, re.I))
            print(f"detail {i}/{len(jobs)}: {job['title']} @ {job['company']}")
            time.sleep(random.uniform(1.0, 2.5))
    return jobs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keywords", default="java selenium")
    parser.add_argument("--location", default="Hyderabad, Telangana, India")
    parser.add_argument("--max-jobs", type=int, default=200)
    parser.add_argument("--no-details", action="store_true", help="skip per-job detail fetch")
    parser.add_argument(
        "--posted-within",
        help="LinkedIn f_TPR filter, e.g. r86400 (24h), r604800 (week), r2592000 (month)",
    )
    parser.add_argument("--out-dir", default=str(Path(__file__).parent / "output"))
    args = parser.parse_args()

    jobs = scrape(args.keywords, args.location, args.max_jobs, not args.no_details, args.posted_within)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "_", f"{args.keywords} {args.location}".lower()).strip("_")
    json_path = out_dir / f"{slug}.json"
    csv_path = out_dir / f"{slug}.csv"
    json_path.write_text(json.dumps(jobs, indent=2, ensure_ascii=False))

    fields = []
    for job in jobs:
        fields += [k for k in job if k not in fields]
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(jobs)
    print(f"Saved {len(jobs)} jobs to {csv_path} and {json_path}")


if __name__ == "__main__":
    main()
