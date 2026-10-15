#!/usr/bin/env python3
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

SOURCES = [
    {
        "name": "Birds of Poole Harbour",
        "url": "https://www.birdsofpooleharbourbookings.co.uk/",
    },
    {
        "name": "National Trust Brownsea",
        "url": "https://www.nationaltrust.org.uk/visit/dorset/brownsea-island/events",
    },
    {
        "name": "Dorset Wildlife Trust",
        "url": "https://www.dorsetwildlifetrust.org.uk/events",
    },
]

MONTH_RE = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)
DATE_RE = re.compile(rf"\b(\d{{1,2}}\s+{MONTH_RE}\s+20\d{{2}})\b", re.I)
TIME_RE = re.compile(
    r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s*(?:-|–|to)\s*"
    r"\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b",
    re.I,
)

HEADERS = {"User-Agent": "Mozilla/5.0 (Home Assistant Photo Picks; +https://onecomposition.com/)"}


def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def walk_json(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_json(child)


def parse_dt(value):
    if not value:
        return None
    try:
        dt = dateparser.parse(str(value), dayfirst=True)
        if dt and dt.tzinfo:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt
    except Exception:
        return None


def location_text(value):
    if isinstance(value, str):
        return clean(value)
    if isinstance(value, dict):
        address = value.get("address")
        if isinstance(address, dict):
            bits = [
                address.get("streetAddress"),
                address.get("addressLocality"),
                address.get("addressRegion"),
                address.get("postalCode"),
            ]
            result = ", ".join(clean(x) for x in bits if clean(x))
            if result:
                return result
        return clean(value.get("name"))
    return ""


def extract_jsonld_events(soup, source):
    events = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text(" ", strip=True)
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        for obj in walk_json(payload):
            typ = obj.get("@type")
            types = typ if isinstance(typ, list) else [typ]
            if "Event" not in types:
                continue
            title = clean(obj.get("name"))
            if not title:
                continue
            start = parse_dt(obj.get("startDate"))
            desc = clean(obj.get("description"))
            loc = location_text(obj.get("location"))
            url = clean(obj.get("url")) or source["url"]
            events.append(
                {
                    "title": title,
                    "start": start,
                    "date_text": start.strftime("%d %b %Y") if start else "",
                    "time_text": start.strftime("%H:%M") if start and (start.hour or start.minute) else "",
                    "location": loc,
                    "description": desc,
                    "url": url,
                    "source": source["name"],
                }
            )
    return events


def extract_heading_events(soup, source):
    events = []
    seen = set()

    for heading in soup.find_all(["h2", "h3", "h4", "h5"]):
        title = clean(heading.get_text(" ", strip=True))
        if len(title) < 4 or len(title) > 140:
            continue

        container = heading
        for _ in range(3):
            if container.parent:
                container = container.parent
        block = clean(container.get_text(" ", strip=True))
        date_match = DATE_RE.search(block)
        if not date_match:
            continue

        key = (title.lower(), date_match.group(1).lower())
        if key in seen:
            continue
        seen.add(key)

        start = parse_dt(date_match.group(1))
        time_match = TIME_RE.search(block)

        link = heading.find("a", href=True) or container.find("a", href=True)
        url = source["url"]
        if link:
            href = link["href"]
            if href.startswith("http"):
                url = href
            elif href.startswith("/"):
                from urllib.parse import urljoin
                url = urljoin(source["url"], href)

        location = ""
        location_match = re.search(
            r"\b(Poole Quay|Poole|Brownsea Island|RSPB Arne|Arne|Wareham|Studland|"
            r"Swanage|Bournemouth|Christchurch|Weymouth|Dorchester|Kimmeridge|"
            r"Portland|Chesil|Lorton Meadows|Upton Country Park)\b",
            block,
            re.I,
        )
        if location_match:
            # Keep the dashboard location concise and deterministic. The old
            # pattern captured the rest of the surrounding sentence as well.
            canonical_locations = {
                "poole quay": "Poole Quay",
                "poole": "Poole",
                "brownsea island": "Brownsea Island",
                "rspb arne": "RSPB Arne",
                "arne": "Arne",
                "wareham": "Wareham",
                "studland": "Studland",
                "swanage": "Swanage",
                "bournemouth": "Bournemouth",
                "christchurch": "Christchurch",
                "weymouth": "Weymouth",
                "dorchester": "Dorchester",
                "kimmeridge": "Kimmeridge, Dorset",
                "portland": "Portland",
                "chesil": "Chesil",
                "lorton meadows": "Lorton Meadows",
                "upton country park": "Upton Country Park",
            }
            location = canonical_locations.get(
                location_match.group(1).lower(),
                clean(location_match.group(1)),
            )

        events.append(
            {
                "title": title,
                "start": start,
                "date_text": date_match.group(1),
                "time_text": clean(time_match.group(1)) if time_match else "",
                "location": location,
                "description": block[:700],
                "url": url,
                "source": source["name"],
            }
        )

    return events


def fetch_events(source):
    response = requests.get(source["url"], headers=HEADERS, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    events = extract_jsonld_events(soup, source)
    events.extend(extract_heading_events(soup, source))
    return events


def score_event(event, now):
    text = clean(
        " ".join(
            [
                event.get("title", ""),
                event.get("description", ""),
                event.get("location", ""),
            ]
        )
    ).lower()

    score = 0

    high = {
        "white-tailed eagle": 60,
        "eagle": 45,
        "osprey": 50,
        "harrier": 45,
        "peregrine": 45,
        "goshawk": 45,
        "owl": 40,
        "raptor": 40,
        "red squirrel": 45,
        "squirrel": 35,
        "wader": 35,
        "wildfowl": 30,
        "seabird": 35,
        "dolphin": 40,
        "whale": 40,
        "seal": 30,
        "deer": 35,
        "bird": 22,
        "wildlife": 18,
    }
    for term, points in high.items():
        if term in text:
            score += points

    for term, points in {
        "safari": 18,
        "cruise": 16,
        "bird walk": 18,
        "birdwatch": 16,
        "lagoon": 12,
        "harbour": 10,
        "migration": 14,
        "photography": 25,
    }.items():
        if term in text:
            score += points

    for term, penalty in {
        "sold out": -80,
        "workshop": -15,
        "craft": -20,
        "storytime": -20,
        "cream tea": -8,
        "online": -50,
    }.items():
        if term in text:
            score += penalty

    start = event.get("start")
    if start:
        delta = (start.date() - now.date()).days
        if delta < 0:
            return -999
        if delta <= 7:
            score += 20
        elif delta <= 21:
            score += 14
        elif delta <= 45:
            score += 8
        elif delta > 120:
            score -= 10

    return score


def icon_for(event):
    text = clean(event.get("title", "") + " " + event.get("description", "")).lower()
    if any(x in text for x in ["eagle", "harrier", "peregrine", "goshawk", "raptor"]):
        return "🦅"
    if "owl" in text:
        return "🦉"
    if "squirrel" in text:
        return "🐿️"
    if any(x in text for x in ["dolphin", "whale"]):
        return "🐬"
    if "seal" in text:
        return "🦭"
    return "🐦"


def lens_fit(event):
    text = clean(event.get("title", "") + " " + event.get("description", "")).lower()
    if any(x in text for x in ["eagle", "osprey", "harrier", "peregrine", "goshawk", "owl", "wader", "wildfowl", "seabird", "squirrel"]):
        return "Excellent for 600mm"
    if any(x in text for x in ["bird", "wildlife", "dolphin", "whale", "seal"]):
        return "Very good for 600mm"
    return "Good long-lens opportunity"


def why_for(event):
    text = clean(event.get("title", "") + " " + event.get("description", "")).lower()
    subjects = []
    mapping = [
        ("eagle", "eagles"),
        ("osprey", "ospreys"),
        ("harrier", "harriers"),
        ("peregrine", "peregrines"),
        ("owl", "owls"),
        ("wader", "waders"),
        ("wildfowl", "wildfowl"),
        ("seabird", "seabirds"),
        ("squirrel", "red squirrels"),
        ("dolphin", "dolphins"),
        ("whale", "whales"),
        ("seal", "seals"),
    ]
    for needle, label in mapping:
        if needle in text and label not in subjects:
            subjects.append(label)
    if subjects:
        return ", ".join(subjects[:4]).capitalize() + "; strong long-lens subject."
    return "Wildlife-focused event with good long-lens potential."


def format_pick(event):
    start = event.get("start")
    if start:
        date_text = start.strftime("%a %d %b").upper()
        time_text = event.get("time_text", "")
        if re.fullmatch(r"\d{2}:\d{2}", time_text or "") and time_text == "00:00":
            time_text = ""
    else:
        date_text = clean(event.get("date_text")).upper()
        time_text = clean(event.get("time_text"))

    return {
        "icon": icon_for(event),
        "title": clean(event.get("title")).strip(" ,;:-").upper(),
        "location": clean(event.get("location")) or event.get("source", "Dorset"),
        "date": date_text,
        "time": time_text,
        "why": why_for(event),
        "lens_fit": lens_fit(event),
        "url": event.get("url"),
        "source": event.get("source"),
    }


def main():
    now = datetime.now()
    candidates = []
    errors = []

    for source in SOURCES:
        try:
            candidates.extend(fetch_events(source))
        except Exception as exc:
            errors.append(f"{source['name']}: {exc}")

    dedup = {}
    for event in candidates:
        key = (
            clean(event.get("title")).lower(),
            event.get("start").date().isoformat() if event.get("start") else clean(event.get("date_text")).lower(),
        )
        if key not in dedup:
            dedup[key] = event

    ranked = sorted(
        dedup.values(),
        key=lambda item: (score_event(item, now), -(item.get("start") or datetime.max).timestamp()),
        reverse=True,
    )

    picks = []
    seen_titles = set()
    for event in ranked:
        score = score_event(event, now)
        if score < 18:
            continue
        title_key = clean(event.get("title")).lower()
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)
        picks.append(format_pick(event))
        if len(picks) == 3:
            break

    payload = {
        "updated": datetime.now(timezone.utc).date().isoformat(),
        "area": "Poole/Dorset, UK",
        "radius_miles": 50,
        "picks": picks,
        "sources": [x["url"] for x in SOURCES],
    }
    if errors:
        payload["source_errors"] = errors

    Path("photo-picks.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
