#!/usr/bin/env python3

"""
Latvijas Pasts suspension monitor
=================================

Checks:

1. Latvijas Pasts "Aktuālā informācija" page.
   Two separate suspension sections are monitored:

   - Tuvo Austrumu galamērķi, uz kuriem nav iespējams
     nosūtīt sūtījumus:

   - Pasta sūtījumu piegāde uz nenoteiktu laiku nav
     pieejama uz sekojošiem galamērķiem:

   Results from both sections are combined and duplicates removed.

2. Latvijas Pasts "Valstu sadalījums" page.

   Countries with a non-empty `warning` field in the embedded
   COUNTRIES JavaScript data are considered suspended.

Output:
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
)

WEBSITE_2_URL = (
    "https://pasts.lv/aizliegumi/"
    "valstu-sadalijums?q=pale&r=0"
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
# Website 1 headings
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
# Website 1 destination mapping
#
# The website uses grammatical Latvian forms in sentences:
#
#   Irāna  -> Irāna
#   Jemena -> Jemenu
#   Sīrija -> Sīriju
#
# Therefore both nominative and sentence forms are accepted.
# ============================================================

WEBSITE_1_NAME_MAP = {
    "Irāna": "Iran",
    "Irānu": "Iran",

    "Jemena": "Yemen",
    "Jemenu": "Yemen",

    "Sīrija": "Syria",
    "Sīriju": "Syria",

    "ASV Mazās aizjūras salas": (
        "United States Minor Outlying Islands"
    ),

    "ASV Mazās aizjūras salām": (
        "United States Minor Outlying Islands"
    ),
}


# Canonical Latvian name used in output.
WEBSITE_1_CANONICAL_NAMES = {
    "Iran": "Irāna",
    "Yemen": "Jemena",
    "Syria": "Sīrija",
    "United States Minor Outlying Islands": (
        "ASV Mazās aizjūras salas"
    ),
}


# ============================================================
# HTTP
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
    Convert HTML entities/NBSP and normalize whitespace.

    This is important because the live page currently contains
    a non-breaking space between some words, for example:

        "piegāde uz"

    instead of:

        "piegāde uz"
    """
    if value is None:
        return ""

    value = html.unescape(str(value))

    # Convert NBSP to an ordinary space.
    value = value.replace("\xa0", " ")

    # Normalize all whitespace.
    value = re.sub(r"\s+", " ", value)

    return value.strip()


# ============================================================
# Website 1 - visible text
# ============================================================

def get_visible_text(html_source):
    """
    Extract visible page text.

    JavaScript/CSS/template content is removed so that only the
    actual page content remains.
    """
    soup = BeautifulSoup(
        html_source,
        "html.parser",
    )

    for tag in soup.find_all(
        ["script", "style", "noscript", "template"]
    ):
        tag.decompose()

    return normalize_text(
        soup.get_text(" ", strip=True)
    )


# ============================================================
# Website 1 - direct section extraction
# ============================================================

def extract_section_sentence(page_text, heading):
    """
    Extract the text immediately following a specific heading.

    The live page uses a simple structure:

        HEADING
        destination list/sentence.

    We intentionally search for the heading itself rather than
    relying on HTML sibling/container structure.

    The returned text ends at the first sentence-ending period.
    """
    normalized_page = normalize_text(page_text)
    normalized_heading = normalize_text(heading)

    # Escape the heading, but allow arbitrary whitespace between
    # its words. This handles normal spaces and NBSPs.
    heading_parts = normalized_heading.split()

    heading_pattern = r"\s+".join(
        re.escape(part)
        for part in heading_parts
    )

    pattern = re.compile(
        heading_pattern
        + r"\s*(.*?)\.",
        re.IGNORECASE | re.DOTALL,
    )

    match = pattern.search(normalized_page)

    if not match:
        raise RuntimeError(
            "Could not find Website 1 section heading:\n"
            + heading
        )

    return normalize_text(match.group(1))


# ============================================================
# Website 1 - country extraction
# ============================================================

def extract_destinations_from_sentence(sentence):
    """
    Find known destination names in a section sentence.

    This deliberately searches the entire sentence rather than
    assuming destinations are separated by commas in a particular
    HTML element.
    """
    normalized_sentence = normalize_text(sentence)

    found = []

    # Longest names first.
    candidates = sorted(
        WEBSITE_1_NAME_MAP.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    )

    for latvian_name, english_name in candidates:

        # Match complete words/phrases.
        pattern = (
            r"(?<!\w)"
            + re.escape(latvian_name)
            + r"(?!\w)"
        )

        if re.search(
            pattern,
            normalized_sentence,
            flags=re.IGNORECASE,
        ):
            if english_name not in found:
                found.append(english_name)

    return found


def check_website_1():
    """
    Check both Website 1 suspension sections and combine them.
    """
    print("Checking Website 1...")

    html_source = fetch_page(WEBSITE_1_URL)

    page_text = get_visible_text(html_source)

    # --------------------------------------------------------
    # FIRST SUSPENSION SECTION
    # --------------------------------------------------------

    section_1_sentence = extract_section_sentence(
        page_text,
        SECTION_1_HEADING,
    )

    print()
    print("Website 1 - first suspension section:")
    print("  ", section_1_sentence)

    section_1_destinations = (
        extract_destinations_from_sentence(
            section_1_sentence
        )
    )

    # --------------------------------------------------------
    # SECOND SUSPENSION SECTION
    # --------------------------------------------------------

    section_2_sentence = extract_section_sentence(
        page_text,
        SECTION_2_HEADING,
    )

    print()
    print("Website 1 - second suspension section:")
    print("  ", section_2_sentence)

    section_2_destinations = (
        extract_destinations_from_sentence(
            section_2_sentence
        )
    )

    # --------------------------------------------------------
    # COMBINE + DEDUPLICATE
    # --------------------------------------------------------

    combined = []

    for destination in (
        section_1_destinations
        + section_2_destinations
    ):
        if destination not in combined:
            combined.append(destination)

    print()
    print(
        "Website 1 combined suspended destinations:",
        len(combined),
    )

    for destination in combined:
        latvian_name = WEBSITE_1_CANONICAL_NAMES.get(
            destination,
            destination,
        )

        print(
            f"  - {latvian_name} — {destination}"
        )

    return combined


# ============================================================
# Website 2 - COUNTRIES extraction
# ============================================================

def extract_countries_array(html_source):
    """
    Extract:

        var COUNTRIES = [ ... ];

    from the Website 2 JavaScript.
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
            "Website 2: COUNTRIES data is not valid JSON."
        ) from exc


# ============================================================
# Website 2 - suspension test
# ============================================================

def country_is_suspended(country):
    """
    Website 2 is considered suspended when the country has a
    non-empty `warning`.

    We intentionally DO NOT use:

        letters == "Nē"

    by itself, because that can indicate a service-specific
    restriction rather than a complete destination suspension.
    """
    warning = country.get("warning")

    if warning is None:
        return False

    if not isinstance(warning, str):
        return bool(warning)

    return bool(warning.strip())


def check_website_2():
    """Check Website 2."""
    print()
    print("Checking Website 2...")

    html_source = fetch_page(WEBSITE_2_URL)

    countries = extract_countries_array(
        html_source
    )

    suspended = []

    for country in countries:

        if not country_is_suspended(country):
            continue

        name_lv = normalize_text(
            country.get("nameLv")
        )

        name_en = normalize_text(
            country.get("nameEn")
        )

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
            f"  - {country['lv']} — {country['en']}"
        )

    return suspended


# ============================================================
# Output
# ============================================================

def build_output(
    website_1,
    website_2,
):
    """
    Build the output_latvia file.
    """
    lines = []

    lines.append(
        "LATVIJAS PASTS — SUSPENDED DESTINATIONS"
    )

    lines.append("")

    # --------------------------------------------------------
    # Website 1
    # --------------------------------------------------------

    lines.append(
        "WEBSITE 1 — AKTUĀLĀ INFORMĀCIJA"
    )

    lines.append(
        f"Source: {WEBSITE_1_URL}"
    )

    lines.append(
        f"Total suspended destinations: {len(website_1)}"
    )

    for destination in website_1:

        latvian_name = WEBSITE_1_CANONICAL_NAMES.get(
            destination,
            destination,
        )

        lines.append(
            f"- {latvian_name} — {destination}"
        )

    lines.append("")

    # --------------------------------------------------------
    # Website 2
    # --------------------------------------------------------

    lines.append(
        "WEBSITE 2 — VALSTU SADALĪJUMS"
    )

    lines.append(
        f"Source: {WEBSITE_2_URL}"
    )

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
    print(
        "Starting Latvijas Pasts suspension monitor..."
    )

    try:

        # ----------------------------------------------------
        # Website 1
        # ----------------------------------------------------

        website_1 = check_website_1()

        # ----------------------------------------------------
        # Website 2
        # ----------------------------------------------------

        website_2 = check_website_2()

        # ----------------------------------------------------
        # Create output
        # ----------------------------------------------------

        output = build_output(
            website_1,
            website_2,
        )

        OUTPUT_FILE.write_text(
            output,
            encoding="utf-8",
        )

        print()
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


# ============================================================
# Entry point
# ============================================================

if __name__ == "__main__":
    main()
