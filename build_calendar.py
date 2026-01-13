from __future__ import annotations

import re
import unicodedata
import requests
from datetime import datetime, timezone
from html import unescape

BASE_HOST = "https://www.basketball-bund.net"
KALM_URL = f"{BASE_HOST}/servlet/KalenderDienst"

# >>> Deine Teams (Teamname, Liga-ID)
TARGETS = [
    ("TSV Grombühl 2", "51127"),
    ("TSV Grombühl", "52205"),
    ("TSV Grombühl AK", "51052"),
    ("TG Veitshöchheim", "49758"),
]

SPT = "-1"  # alle Spieltage

# --- helpers ---
VEVENT_RE = re.compile(r"BEGIN:VEVENT\r?\n.*?\r?\nEND:VEVENT\r?\n", re.DOTALL)

# Dropdown: <option value="425276"SELECTED>TSV Grombühl 2</option>
OPT_RE = re.compile(r'<option\s+value="([^"]+)"[^>]*>(.*?)</option>', re.IGNORECASE | re.DOTALL)

def norm(s: str) -> str:
    """Normalize strings for robust matching (umlauts, whitespace, case)."""
    s = unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return s.casefold()

def fetch_team_id(team_name: str, liga_id: str) -> str:
    """Load schedule page and find ms_liga_id for team_name in cbMannschaftenFilter."""
    url = f"{BASE_HOST}/index.jsp"
    params = {"Action": "101", "liga_id": liga_id}

    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    html = r.text

    # Optional: nur den relevanten <select name="cbMannschaftenFilter">-Block rausziehen
    m = re.search(r'<select[^>]+name="cbMannschaftenFilter"[^>]*>(.*?)</select>', html, re.IGNORECASE | re.DOTALL)
    block = m.group(1) if m else html

    wanted = norm(team_name)
    candidates = []
    for val, label in OPT_RE.findall(block):
        label_clean = norm(re.sub(r"<.*?>", "", label))
        # Skip "- alle Mannschaften -" etc.
        if val.strip() in ("-1", ""):
            continue
        candidates.append((label_clean, val.strip(), re.sub(r"<.*?>", "", label).strip()))

    # 1) exakter Match
    for label_clean, val, original in candidates:
        if label_clean == wanted:
            return val

    # 2) contains match (falls z.B. "TG 1877 Veitshöchheim" vs "TG 1877 Veitshöchheim" o.ä.)
    for label_clean, val, original in candidates:
        if wanted in label_clean or label_clean in wanted:
            return val

    # Debug-Hilfe: zeige die nächsten Treffer (top 10)
    sample = ", ".join([orig for _, _, orig in candidates[:10]])
    raise RuntimeError(
        f"Team '{team_name}' nicht im Dropdown gefunden (liga_id={liga_id}). "
        f"Beispieloptionen: {sample}"
    )

def fetch_ics(liga_id: str, ms_liga_id: str) -> str:
    params = {"typ": "2", "liga_id": liga_id, "ms_liga_id": ms_liga_id, "spt": SPT}
    r = requests.get(KALM_URL, params=params, timeout=60)
    r.raise_for_status()
    text = r.text
    # normalize to CRLF (ICS-konform)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
    return text

def extract_events(ics_text: str) -> list[str]:
    return VEVENT_RE.findall(ics_text)

def prefix_uid(event_text: str, prefix: str) -> str:
    if "UID:" in event_text:
        return re.sub(
            r"^UID:(.+)\r\n",
            lambda m: f"UID:{prefix}-{m.group(1)}\r\n",
            event_text,
            flags=re.MULTILINE,
        )
    uid = f"{prefix}-{abs(hash(event_text))}@basketball-bund.net"
    lines = event_text.split("\r\n")
    out = []
    inserted = False
    for line in lines:
        out.append(line)
        if (not inserted) and line.startswith("DTSTART"):
            out.append(f"UID:{uid}")
            inserted = True
    return "\r\n".join(out)

def ensure_calendar_header() -> str:
    return (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//David//BB Kalender Merge//DE\r\n"
        "CALSCALE:GREGORIAN\r\n"
        "METHOD:PUBLISH\r\n"
        "X-WR-CALNAME:Basketball Spiele\r\n"
        "X-WR-TIMEZONE:Europe/Berlin\r\n"
        "X-PUBLISHED-TTL:PT168H\r\n"  # 7 Tage
    )

def main() -> None:
    merged = []

    for team_name, liga_id in TARGETS:
        ms_id = fetch_team_id(team_name, liga_id)
        ics = fetch_ics(liga_id, ms_id)
        events = extract_events(ics)
        if not events:
            raise RuntimeError(f"Keine VEVENTs gefunden für {team_name} (liga_id={liga_id}, ms_liga_id={ms_id})")

        prefix = f"liga{liga_id}-team{ms_id}"
        for ev in events:
            ev2 = prefix_uid(ev, prefix)

            # Teamname zur besseren Sichtbarkeit in SUMMARY
            ev2 = re.sub(
                r"^SUMMARY:(.+)\r\n",
                lambda m: f"SUMMARY:{m.group(1)} [{team_name}]\r\n",
                ev2,
                flags=re.MULTILINE,
            )
            merged.append(ev2)

    # Dedupe (falls identische Events in mehreren Feeds auftauchen)
    merged = list(dict.fromkeys(merged))

    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = ensure_calendar_header()
    out += f"X-WR-CALDESC:Automatisch generiert. Letztes Update: {now}\r\n"
    out += "".join(merged)
    out += "END:VCALENDAR\r\n"

    with open("calendar.ics", "w", encoding="utf-8", newline="") as f:
        f.write(out)

    print(f"OK: {len(merged)} Events -> calendar.ics")

if __name__ == "__main__":
    main()
