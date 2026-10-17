"""Tell Bing (and the other IndexNow engines) about new pages straight away.

IndexNow is the protocol Bing reads to learn that a page is new or changed
without waiting to crawl it again; one submission reaches every engine
that takes part (Bing, Yandex, Seznam, Naver). Google does not use it: it
reads the sitemap, which is already submitted in Search Console.

This reads the live sitemap and submits:
- every page not submitted before (a new Grand Prix, a new replay);
- the pages that change every week (`FRESH`), at most once a day.

What was submitted is kept in `--state`, so nothing unchanged is sent
again: IndexNow asks not to resubmit pages that have not changed. Run on
the VM after every deploy and every 6 hours (`deploy/deploy_host.py`
installs both). Standard library only, Python 3.10+, so the host's own
Python runs it.
It never fails a deploy: errors are printed and it exits 0.

    python3 deploy/indexnow.py --state /var/lib/pitwall/indexnow.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SITE = "https://fastpitstop.online"
HOST = "fastpitstop.online"
KEY = "57292a32e8821e25ebc9e3b711477fcf"
"""Public by design: IndexNow checks it against `/<KEY>.txt` on the site
(`apps/web/static/<KEY>.txt`). It proves the site owns the submission."""
KEY_LOCATION = f"{SITE}/{KEY}.txt"
ENDPOINT = "https://api.indexnow.org/indexnow"
FRESH = (f"{SITE}/", f"{SITE}/live", f"{SITE}/sessions", f"{SITE}/standings")
"""Pages whose content changes every week (the next race, the latest
replays, the standings): resubmitted at most once a day."""
MAX_URLS = 10_000
"""IndexNow's limit per request."""


def fetch(url: str, timeout: float = 20.0) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "fastpitstop-indexnow/1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8")


def sitemap_urls(xml: str) -> list[str]:
    return [url for url in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml) if url.startswith(SITE)]


def due(urls: list[str], state: dict[str, object], today: str) -> list[str]:
    """New pages, plus the fresh ones not yet submitted today."""
    seen = set(state.get("submitted", []))  # type: ignore[arg-type]
    fresh_day = state.get("fresh_day")
    picked = [url for url in urls if url not in seen]
    if fresh_day != today:
        picked += [url for url in FRESH if url in urls and url not in picked]
    return picked[:MAX_URLS]


def submit(urls: list[str]) -> int:
    body = json.dumps(
        {"host": HOST, "key": KEY, "keyLocation": KEY_LOCATION, "urlList": urls}
    ).encode("utf-8")
    request = urllib.request.Request(
        ENDPOINT,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return int(response.status)
    except urllib.error.HTTPError as error:
        return int(error.code)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True, help="what was submitted, JSON")
    parser.add_argument("--dry-run", action="store_true", help="print what would be sent")
    args = parser.parse_args(argv[1:])

    try:
        if fetch(KEY_LOCATION).strip() != KEY:
            print(f"indexnow: {KEY_LOCATION} does not hold the key; nothing sent")
            return 0
        urls = sitemap_urls(fetch(f"{SITE}/sitemap.xml"))
    except (OSError, urllib.error.URLError) as error:
        print(f"indexnow: could not read the site ({error}); nothing sent")
        return 0

    state: dict[str, object] = {}
    if args.state.exists():
        try:
            state = json.loads(args.state.read_text(encoding="utf-8"))
        except ValueError:
            state = {}
    # timezone.utc, not datetime.UTC: the VM's own Python is 3.10.
    today = datetime.now(timezone.utc).date().isoformat()  # noqa: UP017
    picked = due(urls, state, today)
    if not picked:
        print("indexnow: nothing new")
        return 0
    if args.dry_run:
        print(f"indexnow: would submit {len(picked)} URL(s)")
        for url in picked:
            print(f"  {url}")
        return 0

    status = submit(picked)
    # 200: received; 202: received, key check pending (a first submission).
    if status not in (200, 202):
        print(f"indexnow: submission refused (HTTP {status}); will retry next run")
        return 0
    submitted = sorted(set(state.get("submitted", [])) | set(picked))  # type: ignore[arg-type]
    fresh_day = today if any(url in FRESH for url in picked) else state.get("fresh_day")
    args.state.parent.mkdir(parents=True, exist_ok=True)
    args.state.write_text(
        json.dumps({"submitted": submitted, "fresh_day": fresh_day}, indent=1), encoding="utf-8"
    )
    print(f"indexnow: submitted {len(picked)} URL(s) (HTTP {status})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
