#!/usr/bin/env python3
import json
import math
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
        "default_location": "Poole Harbour",
    },
    {
        "name": "National Trust Brownsea",
        "url": "https://www.nationaltrust.org.uk/visit/dorset/brownsea-island/events",
        "default_location": "Brownsea Island",
    },
    {
        "name": "Dorset Wildlife Trust",
        "url": "https://www.dorsetwildlifetrust.org.uk/events",
        "default_location": "Dorset",
    },
    {
        "name": "Durlston Country Park",
        "url": "https://www.durlston.co.uk/events.aspx",
        "default_location": "Durlston Country Park",
    },
    {
        "name": "Compton Abbas Airfield",
        "url": "https://www.comptonairfield.com/events/",
        "default_location": "Compton Abbas Airfield",
    },
    {
        "name": "Swanage Railway",
        "url": "https://www.swanagerailway.co.uk/events",
        "default_location": "Swanage",
    },
    {
        "name": "The Tank Museum",
        "url": "https://tankmuseum.org/events/",
        "default_location": "Bovington",
    },
    {
        "name": "Visit Dorset - Nature",
        "url": "https://www.visit-dorset.com/whats-on/nature-events/",
        "default_location": "Dorset",
    },
    {
        "name": "Visit Dorset - Art",
        "url": "https://www.visit-dorset.com/whats-on/art-events/",
        "default_location": "Dorset",
    },
    {
        "name": "Visit Dorset - Sport",
        "url": "https://www.visit-dorset.com/whats-on/sporting-events/",
        "default_location": "Dorset",
    },
]

MONTH_RE = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)
DATE_RE = re.compile(
    rf"\b("
    rf"\d{{1,2}}(?:st|nd|rd|th)?\s+{MONTH_RE},?\s+20\d{{2}}"
    rf"|{MONTH_RE}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+20\d{{2}}"
    rf"|\d{{1,2}}[/-]\d{{1,2}}[/-]20\d{{2}}"
    rf")\b",
    re.I,
)

TIME_RE = re.compile(
    r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s*(?:-|–|to)\s*"
    r"\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b",
    re.I,
)

HEADERS = {"User-Agent": "Mozilla/5.0 (Home Assistant Photo Picks; +https://onecomposition.com/)"}


POOLE_COORDS = (50.7150, -1.9872)
RADIUS_MILES = 50

# Approximate venue/town centroids. Used only for distance gating; exact street-level
# precision is unnecessary for a 50-mile photography discovery radius.
LOCATION_COORDS = {
    "poole": (50.7150, -1.9872),
    "poole quay": (50.7122, -1.9865),
    "poole harbour": (50.6940, -1.9940),
    "brownsea island": (50.6918, -1.9716),
    "bournemouth": (50.7192, -1.8808),
    "christchurch": (50.7358, -1.7813),
    "wareham": (50.6870, -2.1100),
    "arne": (50.6923, -2.0386),
    "rspb arne": (50.6923, -2.0386),
    "studland": (50.6410, -1.9520),
    "swanage": (50.6095, -1.9567),
    "durlston country park": (50.5975, -1.9490),
    "kimmeridge": (50.6178, -2.1269),
    "kimmeridge, dorset": (50.6178, -2.1269),
    "dorchester": (50.7107, -2.4370),
    "weymouth": (50.6090, -2.4540),
    "portland": (50.5480, -2.4430),
    "chesil": (50.6260, -2.5160),
    "bovington": (50.6975, -2.2357),
    "the tank museum": (50.6975, -2.2357),
    "compton abbas": (50.9672, -2.1536),
    "compton abbas airfield": (50.9672, -2.1536),
    "ashmore": (50.9990, -2.1370),
    "west bay": (50.7106, -2.7630),
    "lyme regis": (50.7252, -2.9345),
    "lorton meadows": (50.6204, -2.4666),
    "upton country park": (50.7390, -2.0370),
    "yeovilton": (51.0094, -2.6388),
    "rnas yeovilton": (51.0094, -2.6388),
}

CATEGORY_PRIORITY = ["aviation", "wildlife", "watersports", "photography", "heritage", "other"]


def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()

def normalize_title(value):
    value = clean(value).lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


GENERIC_HEADING_TITLES = {
    "cookie consent",
    "event summary",
    "events",
    "what s on",
    "what's on",
    "more events",
    "upcoming events",
    "latest events",
    "related events",
    "event details",
    "event information",
    "find out more",
    "book now",
    "learn more",
}


def is_generic_heading_title(value):
    normalized = normalize_title(value)
    return normalized in {normalize_title(x) for x in GENERIC_HEADING_TITLES}


def is_non_event_heading(value, source=None):
    normalized = normalize_title(value)

    if is_generic_heading_title(value):
        return True

    # Reject calendar/day labels that some listing pages expose as headings,
    # e.g. "Monday 19th", "Tuesday 20th", etc.
    if re.fullmatch(
        r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+\d{1,2}(?:st|nd|rd|th)?",
        normalized,
    ):
        return True

    # Reject bare venue/location headings. These are structural labels, not
    # actual events, and can otherwise outrank real events because their parent
    # block contains wildlife-related text.
    source_default = normalize_title((source or {}).get("default_location", ""))
    if source_default and normalized == source_default:
        return True

    bare_places = {
        "brownsea island",
        "durlston country park",
        "poole harbour",
        "poole quay",
        "compton abbas airfield",
        "the tank museum",
        "swanage",
        "dorset",
    }
    if normalized in bare_places:
        return True

    return False


def resolve_coords(location):
    text = clean(location).lower()
    if not text:
        return None
    # Prefer the longest known place name to avoid matching "poole" inside
    # "poole quay".
    for key in sorted(LOCATION_COORDS, key=len, reverse=True):
        if key in text:
            return LOCATION_COORDS[key]
    return None


def distance_miles(location):
    coords = resolve_coords(location)
    if not coords:
        return None
    lat1, lon1 = POOLE_COORDS
    lat2, lon2 = coords
    r = 3958.7613
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def category_for(event):
    text = clean(" ".join([
        event.get("title", ""),
        event.get("description", ""),
        event.get("location", ""),
    ])).lower()

    if any(x in text for x in [
        "air show", "airshow", "aircraft", "aviation", "warbird", "wingwalking",
        "aerobatic", "helicopter", "spitfire", "red arrows", "black cats",
    ]):
        return "aviation"
    if any(x in text for x in [
        "windsurf", "kitesurf", "regatta", "sailing", "powerboat", "watersport",
        "surf", "kite festival",
    ]):
        return "watersports"
    if any(x in text for x in [
        "eagle", "osprey", "harrier", "peregrine", "goshawk", "owl", "raptor",
        "squirrel", "wader", "wildfowl", "seabird", "dolphin", "whale", "seal",
        "deer", "bird", "wildlife",
    ]):
        return "wildlife"
    if any(x in text for x in ["photography", "photo walk", "camera"]):
        return "photography"
    if any(x in text for x in [
        "steam train", "steam locomotive", "railway", "tank", "military vehicle",
        "heritage",
    ]):
        return "heritage"
    return "other"



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
            loc = location_text(obj.get("location")) or source.get("default_location", "")
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
        if is_non_event_heading(title, source):
            continue

        # Use the nearest ancestor that contains a date instead of blindly
        # climbing three levels. This keeps each title associated with its own
        # event card and avoids borrowing a date/time from a neighbouring event.
        container = heading
        block = ""
        date_match = None
        for _ in range(5):
            candidate = container.parent if container.parent else container
            candidate_block = clean(candidate.get_text(" ", strip=True))
            candidate_date = DATE_RE.search(candidate_block)

            if candidate_date and len(candidate_block) <= 1800:
                container = candidate
                block = candidate_block
                date_match = candidate_date
                break

            if candidate is container:
                break
            container = candidate

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
            r"Portland|Chesil|Lorton Meadows|Upton Country Park|Bovington|"
            r"Compton Abbas|Ashmore|Yeovilton|West Bay|Lyme Regis)\b",
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
                "bovington": "Bovington",
                "compton abbas": "Compton Abbas Airfield",
                "ashmore": "Compton Abbas Airfield",
                "yeovilton": "RNAS Yeovilton",
                "west bay": "West Bay",
                "lyme regis": "Lyme Regis",
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
                "location": location or source.get("default_location", ""),
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
        "photography": 30,
        "photo walk": 28,
        "camera": 12,
        "air show": 55,
        "airshow": 55,
        "aircraft": 35,
        "aviation": 35,
        "warbird": 45,
        "wingwalking": 45,
        "aerobatic": 40,
        "helicopter": 32,
        "spitfire": 45,
        "red arrows": 55,
        "black cats": 45,
        "flying scotsman": 35,
        "steam locomotive": 28,
        "steam train": 25,
        "tank": 28,
        "military vehicle": 28,
        "windsurf": 35,
        "kitesurf": 35,
        "kite festival": 32,
        "regatta": 28,
        "sailing": 22,
        "powerboat": 35,
        "watersport": 25,
        "surf": 20,
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

    # Hard exclusions and stronger practical-photo quality signals.
    if any(term in text for term in ["cancelled", "canceled", "event cancelled", "event canceled"]):
        return -999
    if "sold out" in text:
        return -999

    if any(term in text for term in ["outdoor", "outside", "open air", "nature reserve", "airfield", "harbour", "coast"]):
        score += 8
    if any(term in text for term in ["spectator", "public viewing", "viewing area", "guided walk", "boat trip", "cruise"]):
        score += 6
    if any(term in text for term in ["night", "evening"]) and "photography" not in text:
        score -= 4

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

        # Daylight/action events are generally better matches for the user's
        # long-lens outdoor photography.
        if 8 <= start.hour <= 17:
            score += 5
        elif start.hour >= 20 or start.hour <= 5:
            score -= 5

    dist = distance_miles(event.get("location", ""))
    event["distance_miles"] = dist
    if dist is not None:
        if dist > RADIUS_MILES:
            return -999
        if dist <= 10:
            score += 10
        elif dist <= 25:
            score += 6
        elif dist <= 40:
            score += 2

    return score


def icon_for(event):
    text = clean(event.get("title", "") + " " + event.get("description", "")).lower()
    if any(x in text for x in ["air show", "airshow", "aircraft", "aviation", "warbird", "wingwalking", "aerobatic", "helicopter", "spitfire", "red arrows", "black cats"]):
        return "✈️"
    if any(x in text for x in ["windsurf", "kitesurf", "regatta", "sailing", "powerboat", "watersport", "surf"]):
        return "🌊"
    if any(x in text for x in ["flying scotsman", "steam locomotive", "steam train", "railway"]):
        return "🚂"
    if any(x in text for x in ["tank", "military vehicle"]):
        return "📸"
    if "photography" in text or "photo walk" in text:
        return "📷"
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
    if any(x in text for x in ["air show", "airshow", "aircraft", "aviation", "warbird", "wingwalking", "aerobatic", "helicopter", "spitfire", "red arrows", "black cats"]):
        return "Excellent for 600mm"
    if any(x in text for x in ["windsurf", "kitesurf", "regatta", "powerboat", "watersport", "flying scotsman", "steam locomotive", "steam train", "tank"]):
        return "Very good for 600mm"
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
    if any(x in text for x in ["air show", "airshow", "aircraft", "aviation", "warbird", "wingwalking", "aerobatic", "helicopter", "spitfire", "red arrows", "black cats"]):
        return "Aircraft/action subject; excellent long-lens photography potential."
    if any(x in text for x in ["windsurf", "kitesurf", "regatta", "sailing", "powerboat", "watersport", "surf"]):
        return "Fast-moving watersports/action subject with strong photography potential."
    if any(x in text for x in ["flying scotsman", "steam locomotive", "steam train", "railway"]):
        return "Heritage railway subject with strong action and environmental photography potential."
    if any(x in text for x in ["tank", "military vehicle"]):
        return "Historic vehicle/action subject with strong photography potential."
    if "photography" in text or "photo walk" in text:
        return "Photography-focused event."
    return "Visually strong event with good photography potential."


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
        "category": category_for(event),
        "distance_miles": (
            round(event.get("distance_miles"), 1)
            if event.get("distance_miles") is not None
            else None
        ),
    }


def main():
    now = datetime.now()
    candidates = []
    source_health = []

    for source in SOURCES:
        health = {
            "name": source["name"],
            "url": source["url"],
            "status": "ok",
            "events_found": 0,
        }
        try:
            events = fetch_events(source)
            health["events_found"] = len(events)
            if not events:
                health["status"] = "empty"
            candidates.extend(events)
        except Exception as exc:
            health["status"] = "error"
            health["error"] = clean(exc)[:240]
        source_health.append(health)

    # Stronger duplicate suppression: normalized title + date + location.
    # This collapses listing-page/event-page duplicates without merging
    # genuinely different events that happen on the same day.
    dedup = {}
    for event in candidates:
        date_key = (
            event.get("start").date().isoformat()
            if event.get("start")
            else clean(event.get("date_text")).lower()
        )
        location_key = clean(event.get("location")).lower()
        key = (normalize_title(event.get("title")), date_key, location_key)
        if key not in dedup:
            dedup[key] = event
        else:
            # Prefer the richer record when two pages describe the same event.
            current = dedup[key]
            if len(clean(event.get("description"))) > len(clean(current.get("description"))):
                dedup[key] = event

    scored = []
    for event in dedup.values():
        score = score_event(event, now)
        if score >= 20:
            event["_score"] = score
            event["_category"] = category_for(event)
            scored.append(event)

    ranked = sorted(
        scored,
        key=lambda item: (
            item["_score"],
            -(item.get("start") or datetime.max).timestamp(),
        ),
        reverse=True,
    )

    # Keep a reserve pool of the ten strongest unique candidates.
    reserve_pool = ranked[:10]

    # Prefer category diversity in the three dashboard slots. First take the
    # strongest event from distinct categories, then fill any remaining slots
    # purely by score.
    picks_events = []
    used_categories = set()
    used_keys = set()

    for event in reserve_pool:
        category = event["_category"]
        key = (
            normalize_title(event.get("title")),
            event.get("start").date().isoformat() if event.get("start") else clean(event.get("date_text")),
            clean(event.get("location")).lower(),
        )
        if category not in used_categories:
            picks_events.append(event)
            used_categories.add(category)
            used_keys.add(key)
        if len(picks_events) == 3:
            break

    if len(picks_events) < 3:
        for event in reserve_pool:
            key = (
                normalize_title(event.get("title")),
                event.get("start").date().isoformat() if event.get("start") else clean(event.get("date_text")),
                clean(event.get("location")).lower(),
            )
            if key in used_keys:
                continue
            picks_events.append(event)
            used_keys.add(key)
            if len(picks_events) == 3:
                break

    picks = [format_pick(event) for event in picks_events]
    reserves = [
        format_pick(event)
        for event in reserve_pool
        if event not in picks_events
    ][:7]

    payload = {
        "updated": datetime.now(timezone.utc).date().isoformat(),
        "area": "Poole/Dorset, UK",
        "radius_miles": RADIUS_MILES,
        "picks": picks,
        "reserves": reserves,
        "source_health": source_health,
        "sources": [x["url"] for x in SOURCES],
    }

    Path("photo-picks.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
