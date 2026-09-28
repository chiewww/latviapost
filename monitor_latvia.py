#!/usr/bin/env python3

"""
Latvijas Pasts suspension monitor
---------------------------------

Checks two Latvijas Pasts pages:

1. Aktuālā informācija par pārrobežu sūtījumu piegādes iespējām
   - "Tuvo Austrumu galamērķi, uz kuriem nav iespējams nosūtīt sūtījumus:"
   - "Pasta sūtījumu piegāde uz nenoteiktu laiku nav pieejama uz sekojošiem galamērķiem:"

2. Valstu sadalījums
   - Uses the embedded COUNTRIES JavaScript data.
   - A country is considered suspended when it has a non-empty "warning".

The output is written to:
    output_latvia
"""

# ============================================================
# Imports
# ============================================================

import html
import json
import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup


# ============================================================
# Configuration
# ============================================================

WEBSITE_1_URL = (
    "https://pasts.lv/aizliegumi/"
    "aktuala-informacija-par-parrobezu-sutijumu-piegades-iespejam"
    "#aktuala-informacija-par-parrobezu-sutijumu-piegades-iespejam"
)

WEBSITE_2_URL = (
    "https://pasts.lv/aizliegumi/"
    "valstu-sadalijums?q=pale&r=0#valstu-sadalijums"
)

OUTPUT_FILE = Path("output_latvia")

REQUEST_TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0 Safari/537.36"
    ),
    "Accept-Language": "lv-LV,lv;q=0.9,en;q=0.8",
}


# ============================================================
# Website 1 section headings
# ============================================================

SECTION_1_HEADING = (
    "Tuvo Austrumu galamērķi, uz kuriem nav iespējams "
    "nosūtīt sūtījumus:"
)

SECTION_2_HEADING = (
    "Pasta sūtījumu piegāde uz nenoteiktu laiku nav "
    "pieejama uz sekojošiem galamērķiem:"
)


# ============================================================
# Known Website 1 Latvian -> English country names
#
# The website sometimes uses Latvian grammatical forms:
#   Jemena -> Jemenu
#   Sīrija -> Sīriju
#   Irāna -> Irānu
#
# Keep both forms so changes in the page wording are detected.
# ============================================================

WEBSITE_1_NAME_MAP = {
    "Irāna": "Iran",
    "Irānu": "Iran",

    "Jemena": "Yemen",
    "Jemenu": "Yemen",

    "Sīrija": "Syria",
    "Sīriju": "Syria",

    "ASV Mazās aizjūras salas":
        "United States Minor Outlying Islands",
}


# ============================================================
# HTTP helper
# ============================================================

def fetch_page(url):
    """Download a page and return its HTML."""
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.text


# ============================================================
# Text normalization
# ============================================================

def normalize_text(value):
    """
    Normalize whitespace and HTML entities while preserving
    Latvian characters.
    """
    if value is None:
        return ""

    value = html.unescape(str(value))
    value = value.replace("\xa0", " ")

    # Normalize whitespace.
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def normalize_for_match(value):
    """
    More aggressive normalization for matching headings.
    """
    value = normalize_text(value)

    return value.casefold()


# ============================================================
# Website 1 helpers
# ============================================================

def find_heading_positions(text, heading):
    """
    Find every occurrence of a heading in normalized page text.

    Returns character positions.
    """
    normalized_text = normalize_for_match(text)
    normalized_heading = normalize_for_match(heading)

    positions = []

    start = 0

    while True:
        position = normalized_text.find(normalized_heading, start)

        if position == -1:
            break

        positions.append(position)
        start = position + len(normalized_heading)

    return positions


def get_visible_page_text(html_source):
    """
    Extract visible text from the page.

    Script/style/noscript contents are removed because they can
    otherwise interfere with section detection.
    """
    soup = BeautifulSoup(html_source, "html.parser")

    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()

    return normalize_text(soup.get_text(" ", strip=True))


def extract_country_names_from_section(section_text):
    """
    Extract Website 1 destination names from one section.

    The current page contains four relevant destinations. We
    deliberately search the entire section rather than relying
    on a particular HTML container/list structure.

    This makes the parser resilient to changes such as:
      <ul><li>...</li></ul>
      <p>...</p>
      <div>...</div>
      or inline text.
    """
    found = []

    normalized_section = normalize_for_match(section_text)

    # Longest names first, so multi-word destinations are matched
    # before shorter possible fragments.
    candidates = sorted(
        WEBSITE_1_NAME_MAP.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    )

    for latvian_name, english_name in candidates:
        normalized_name = normalize_for_match(latvian_name)

        # Use a Unicode-aware boundary check.
        pattern = r"(?<!\w)" + re.escape(normalized_name) + r"(?!\w)"

        if re.search(pattern, normalized_section):
            if english_name not in found:
                found.append(english_name)

    return found


def extract_website_1_sections(html_source):
    """
    Extract BOTH Website 1 suspension sections independently.

    Important:
    We do NOT assume that the second section is a sibling of the
    first section in the HTML.

    Instead, we work with the complete visible page text and use
    the exact heading positions to define each section.
    """
    page_text = get_visible_page_text(html_source)

    # Find both headings in the actual rendered text.
    heading_1_positions = find_heading_positions(
        page_text,
        SECTION_1_HEADING,
    )

    heading_2_positions = find_heading_positions(
        page_text,
        SECTION_2_HEADING,
    )

    if not heading_1_positions:
        raise RuntimeError(
            "Website 1: could not find the first suspension heading."
        )

    if not heading_2_positions:
        raise RuntimeError(
            "Website 1: could not find the second suspension heading."
        )

    # Use the first occurrence of each exact heading.
    heading_1_start = heading_1_positions[0]
    heading_2_start = heading_2_positions[0]

    heading_1_end = heading_1_start + len(
        normalize_text(SECTION_1_HEADING)
    )

    heading_2_end = heading_2_start + len(
        normalize_text(SECTION_2_HEADING)
    )

    # --------------------------------------------------------
    # Section 1
    #
    # Everything after heading 1 and before heading 2 belongs
    # to the first section.
    # --------------------------------------------------------

    if heading_1_end < heading_2_start:
        section_1_text = page_text[
            heading_1_end:heading_2_start
        ]
    else:
        # Defensive fallback if the order changes.
        section_1_text = page_text[heading_1_end:]

    # --------------------------------------------------------
    # Section 2
    #
    # Everything after heading 2 is searched for destinations.
    #
    # We don't need to know the HTML structure. We only look
    # for destination names that occur in this section.
    # --------------------------------------------------------

    section_2_text = page_text[heading_2_end:]

    section_1_destinations = extract_country_names_from_section(
        section_1_text
    )

    section_2_destinations = extract_country_names_from_section(
        section_2_text
    )

    return section_1_destinations, section_2_destinations


def check_website_1():
    """
    Check Website 1 and return one combined, deduplicated list.
    """
    print("Checking Website 1...")

    html_source = fetch_page(WEBSITE_1_URL)

    section_1, section_2 = extract_website_1_sections(
        html_source
    )

    print(
        "Website 1 section 1 suspended destinations:",
        len(section_1),
    )

    for destination in section_1:
        print("  -", destination)

    print(
        "Website 1 section 2 suspended destinations:",
        len(section_2),
    )

    for destination in section_2:
        print("  -", destination)

    # --------------------------------------------------------
    # Combine both sections and remove duplicates.
    # --------------------------------------------------------

    combined = []

    for destination in section_1 + section_2:
        if destination not in combined:
            combined.append(destination)

    print(
        "Website 1 combined suspended destinations:",
        len(combined),
    )

    for destination in combined:
        print("  -", destination)

    return combined


# ============================================================
# Website 2
# ============================================================

def extract_countries_array(html_source):
    """
    Extract the embedded JavaScript:

        var COUNTRIES = [ ... ];

    The array is JSON-compatible, so it can be decoded directly.
    """
    pattern = re.compile(
        r"\bvar\s+COUNTRIES\s*=\s*(\[.*?\])\s*;",
        re.DOTALL,
    )

    match = pattern.search(html_source)

    if not match:
        raise RuntimeError(
            "Website 2: could not find embedded COUNTRIES data."
        )

    countries_json = match.group(1)

    try:
        return json.loads(countries_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Website 2: COUNTRIES data could not be parsed as JSON."
        ) from exc


def country_is_suspended(country):
    """
    Determine whether Website 2 explicitly marks the country with
    a warning.

    This is intentionally based on `warning`, NOT simply on
    letters == "Nē" or packets == "Nē".

    Some countries can have restricted letter services without
    the entire destination being suspended.
    """
    warning = country.get("warning")

    if warning is None:
        return False

    if not isinstance(warning, str):
        return bool(warning)

    return bool(warning.strip())


def check_website_2():
    """
    Check Website 2 using the embedded COUNTRIES array.
    """
    print("Checking Website 2...")

    html_source = fetch_page(WEBSITE_2_URL)

    countries = extract_countries_array(html_source)

    suspended = []

    for country in countries:
        if country_is_suspended(country):
            name_lv = normalize_text(country.get("nameLv"))
            name_en = normalize_text(country.get("nameEn"))

            if not name_lv:
                continue

            suspended.append(
                {
                    "lv": name_lv,
                    "en": name_en,
                }
            )

    print(
        "Website 2 suspended countries:",
        len(suspended),
    )

    for country in suspended:
        print(
            "  -",
            country["lv"],
            "—",
            country["en"],
        )

    return suspended


# ============================================================
# Output
# ============================================================

def build_output(website_1, website_2):
    """
    Build the final changedetection.io text file.
    """
    lines = []

    lines.append("LATVIJAS PASTS — SUSPENDED DESTINATIONS")
    lines.append("")
    lines.append("WEBSITE 1 — AKTUĀLĀ INFORMĀCIJA")
    lines.append(f"Source: {WEBSITE_1_URL}")
    lines.append(
        f"Total suspended destinations: {len(website_1)}"
    )

    for destination in website_1:
        # Find Latvian name corresponding to the English name.
        latvian_name = next(
            (
                lv
                for lv, en in WEBSITE_1_NAME_MAP.items()
                if en == destination
                and lv not in {
                    "Irānu",
                    "Jemenu",
                    "Sīriju",
                }
            ),
            destination,
        )

        lines.append(
            f"- {latvian_name} — {destination}"
        )

    lines.append("")
    lines.append("WEBSITE 2 — VALSTU SADALĪJUMS")
    lines.append(f"Source: {WEBSITE_2_URL}")
    lines.append(
        f"Total suspended countries: {len(website_2)}"
    )

    for country in website_2:
        lines.append(
            f"- {country['lv']} — {country['en']}"
        )

    lines.append("")

    return "\n".join(lines)


# ============================================================
# Main
# ============================================================

def main():
    print("Starting Latvijas Pasts suspension monitor...")
    print()

    try:
        # ----------------------------------------------------
        # Website 1
        # ----------------------------------------------------
        website_1 = check_website_1()

        print()

        # ----------------------------------------------------
        # Website 2
        # ----------------------------------------------------
        website_2 = check_website_2()

        print()

        # ----------------------------------------------------
        # Build and save output
        # ----------------------------------------------------
        output = build_output(
            website_1,
            website_2,
        )

        OUTPUT_FILE.write_text(
            output,
            encoding="utf-8",
        )

        print(
            f"Output written to {OUTPUT_FILE}"
        )

        print()
        print("Done.")

    except requests.RequestException as exc:
        print(
            f"ERROR: HTTP request failed: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)

    except Exception as exc:
        print(
            f"ERROR: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
