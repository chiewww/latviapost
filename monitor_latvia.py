#!/usr/bin/env python3

"""
Latvijas Pasts suspension monitor
=================================

Checks:

1. Latvijas Pasts "Aktuala informacija" page (Website 1).

   Two sections are monitored:

   a) "Galamerki, uz kuriem piegade nav pieejama uz nenoteiktu laiku"
      Every country listed is reported as a SUSPENDED DESTINATION,
      in Latvian and English.

   b) "Ipasi nosacijumi sutijumu noformesanai"
      Every item is shown as text only (Latvian + English).
      These countries are NOT necessarily suspended.

2. Latvijas Pasts "Valstu sadalijums" page (Website 2).

   Countries with a non-empty `warning` field in the embedded
   COUNTRIES JavaScript data are considered suspended.

   Website 2 also supplies the Latvian -> English country-name
   lookup used for Website 1.

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

HEADING_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6"]


# ============================================================
# Website 1 section headings (Latvian page)
# ============================================================

# Section A - suspended destinations.
SUSPENDED_HEADING = (
    "Galamērķi, uz kuriem piegāde nav pieejama "
    "uz nenoteiktu laiku"
)

# Section B - special conditions (text only, not suspensions).
SPECIAL_CONDITIONS_HEADING = (
    "Īpaši nosacījumi sūtījumu noformēšanai"
)


# ============================================================
# Latvian -> English country names (fallback)
#
# The live Website 2 data (nameLv / nameEn) is used first.
# This table is only used when a name is not found there.
# ============================================================

FALLBACK_COUNTRY_NAMES_EN = {
    "ASV Mazās aizjūras salas": (
        "United States Minor Outlying Islands"
    ),
    "Irāna": "Iran",
    "Jemena": "Yemen",
    "Sīrija": "Syria",
    "Argentīna": "Argentina",
}


# ============================================================
# English translations of the "special conditions" items
#
# Key   : the full Latvian item text (title + body).
#         Matching ignores case, spaces and punctuation.
# Value : the English text.
#
# If the page shows a NEW or CHANGED item that is not listed here,
# the output shows the Latvian text plus a note that no English
# translation exists yet. Add the new item to this table to fix it.
# ============================================================

SPECIAL_CONDITIONS_EN = {
    (
        "Krievija un Baltkrievija: Sūtījumus, kas satur priekšmetus "
        "(sīkpakas un pakas), iespējams noformēt tikai klātienē "
        "pasta nodaļās."
    ): (
        "Russia and Belarus: Shipments containing items "
        "(small packets and parcels) can only be processed "
        "in person at post offices."
    ),
    (
        "Angilja: Pašlaik pasta operators nodrošina tikai "
        "paku piegādi."
    ): (
        "Anguilla: Currently the postal operator provides "
        "parcel delivery only."
    ),
    (
        "Apdrošinātas sīkpakas: Pārrobežu apdrošinātas sīkpakas "
        "pašlaik iespējams nosūtīt tikai uz Austriju un Bulgāriju."
    ): (
        "Insured small packets: Cross-border insured small packets "
        "can currently only be sent to Austria and Bulgaria."
    ),
}

NO_TRANSLATION_NOTE = (
    "[English translation not available yet - new or changed text, "
    "see Latvian original]"
)


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
    """Unescape HTML entities, convert NBSP, collapse whitespace."""
    if value is None:
        return ""

    value = html.unescape(str(value))
    value = value.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def match_key(value):
    """
    Key for comparing texts: lower-case, with every character that
    is not a letter or digit removed. Tolerates small differences
    in spacing and punctuation.
    """
    return re.sub(r"\W+", "", normalize_text(value)).casefold()


# ============================================================
# Website 1 - section helpers
# ============================================================

def make_soup(html_source):
    """Parse HTML and drop script/style/template content."""
    soup = BeautifulSoup(html_source, "html.parser")

    for tag in soup.find_all(
        ["script", "style", "noscript", "template"]
    ):
        tag.decompose()

    return soup


def find_heading(soup, heading_text):
    """Find the heading tag whose text equals heading_text."""
    target = match_key(heading_text)

    for tag in soup.find_all(HEADING_TAGS):
        if match_key(tag.get_text(" ", strip=True)) == target:
            return tag

    raise RuntimeError(
        "Could not find Website 1 section heading:\n"
        + heading_text
    )


def get_section_items(soup, heading_text):
    """
    Return the <li> items of the first list that follows the
    heading, stopping if another heading comes first.
    """
    heading = find_heading(soup, heading_text)

    for element in heading.find_all_next(
        ["ul", "ol"] + HEADING_TAGS
    ):
        if element.name in HEADING_TAGS:
            break

        return element.find_all("li", recursive=False)

    return []


# ============================================================
# Website 1 - suspended destinations
# ============================================================

def get_suspended_destinations(soup):
    """
    Return the Latvian names listed under the
    'delivery not available indefinitely' heading.
    """
    items = get_section_items(soup, SUSPENDED_HEADING)

    names = []

    for item in items:
        name = normalize_text(item.get_text(" ", strip=True))

        if name and name not in names:
            names.append(name)

    if not names:
        raise RuntimeError(
            "Website 1: the suspended destinations list is empty "
            "or could not be read."
        )

    return names


def english_country_name(latvian_name, lv_to_en):
    """Look up the English name of a Latvian country name."""
    key = latvian_name.casefold()

    if key in lv_to_en and lv_to_en[key]:
        return lv_to_en[key]

    for name, english in FALLBACK_COUNTRY_NAMES_EN.items():
        if name.casefold() == key:
            return english

    print(
        f"WARNING: no English name found for '{latvian_name}'",
        file=sys.stderr,
    )

    return "(English name not found)"


# ============================================================
# Website 1 - special conditions (text only)
# ============================================================

def get_special_conditions(soup):
    """
    Return every item of the special-conditions list as a dict
    with the Latvian text and its English translation.
    """
    items = get_section_items(soup, SPECIAL_CONDITIONS_HEADING)

    results = []

    for item in items:

        # Line breaks become spaces so words do not run together.
        for br in item.find_all("br"):
            br.replace_with(" ")

        full_text = normalize_text(item.get_text(""))

        if not full_text:
            continue

        # Bold lead-in (e.g. the country or topic) if present.
        title = ""
        strong = item.find(["strong", "b"])

        if strong is not None:
            candidate = normalize_text(
                strong.get_text(" ", strip=True)
            )

            if candidate and full_text.startswith(candidate):
                title = candidate

        if title:
            body = full_text[len(title):].strip(" :-–—")
            latvian = f"{title}: {body}" if body else title
        else:
            latvian = full_text

        english = None

        for lv_text, en_text in SPECIAL_CONDITIONS_EN.items():
            if match_key(lv_text) == match_key(latvian):
                english = en_text
                break

        if english is None:
            print(
                "WARNING: no English translation for: " + latvian,
                file=sys.stderr,
            )
            english = NO_TRANSLATION_NOTE

        results.append(
            {
                "lv": latvian,
                "en": english,
            }
        )

    if not results:
        raise RuntimeError(
            "Website 1: the special conditions list is empty "
            "or could not be read."
        )

    return results


# ============================================================
# Website 1 - main check
# ============================================================

def check_website_1(lv_to_en):
    """
    Check Website 1. Returns (suspended, special_conditions).

    suspended           : list of {"lv": ..., "en": ...}
    special_conditions  : list of {"lv": ..., "en": ...}
    """
    print("Checking Website 1...")

    html_source = fetch_page(WEBSITE_1_URL)
    soup = make_soup(html_source)

    # ---- Suspended destinations --------------------------------

    suspended = []

    for latvian_name in get_suspended_destinations(soup):
        suspended.append(
            {
                "lv": latvian_name,
                "en": english_country_name(latvian_name, lv_to_en),
            }
        )

    print()
    print(
        "Website 1 suspended destinations:",
        len(suspended),
    )

    for country in suspended:
        print(f"  - {country['lv']} — {country['en']}")

    # ---- Special conditions ------------------------------------

    special = get_special_conditions(soup)

    print()
    print(
        "Website 1 special conditions (text only):",
        len(special),
    )

    for entry in special:
        print(f"  - LV: {entry['lv']}")
        print(f"    EN: {entry['en']}")

    return suspended, special


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

    try:
        return json.loads(match.group(1))

    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Website 2: COUNTRIES data is not valid JSON."
        ) from exc


def country_is_suspended(country):
    """
    Suspended when the country has a non-empty `warning`.

    `letters == "Nē"` is deliberately NOT used, because it can
    indicate a service-specific restriction only.
    """
    warning = country.get("warning")

    if warning is None:
        return False

    if not isinstance(warning, str):
        return bool(warning)

    return bool(warning.strip())


def check_website_2():
    """
    Check Website 2.

    Returns (suspended, lv_to_en):

    suspended : list of {"lv": ..., "en": ...}
    lv_to_en  : dict, lower-case Latvian name -> English name
    """
    print()
    print("Checking Website 2...")

    html_source = fetch_page(WEBSITE_2_URL)

    countries = extract_countries_array(html_source)

    suspended = []
    lv_to_en = {}

    for country in countries:

        name_lv = normalize_text(country.get("nameLv"))
        name_en = normalize_text(country.get("nameEn"))

        if name_lv and name_en:
            lv_to_en[name_lv.casefold()] = name_en

        if not country_is_suspended(country):
            continue

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
        print(f"  - {country['lv']} — {country['en']}")

    return suspended, lv_to_en


# ============================================================
# Output
# ============================================================

def build_output(
    website_1_suspended,
    website_1_special,
    website_2,
):
    """Build the output_latvia file."""
    lines = []

    lines.append("LATVIJAS PASTS — SUSPENDED DESTINATIONS")
    lines.append("")

    # --------------------------------------------------------
    # Website 1 - suspended destinations
    # --------------------------------------------------------

    lines.append("WEBSITE 1 — AKTUĀLĀ INFORMĀCIJA")
    lines.append(f"Source: {WEBSITE_1_URL}")
    lines.append("")

    lines.append("SUSPENDED DESTINATIONS")
    lines.append(
        "(Galamērķi, uz kuriem piegāde nav pieejama "
        "uz nenoteiktu laiku)"
    )
    lines.append(
        "Total suspended destinations: "
        f"{len(website_1_suspended)}"
    )

    for country in website_1_suspended:
        lines.append(f"- {country['lv']} — {country['en']}")

    lines.append("")

    # --------------------------------------------------------
    # Website 1 - special conditions (text only)
    # --------------------------------------------------------

    lines.append("SPECIAL CONDITIONS FOR PROCESSING SHIPMENTS")
    lines.append("(Īpaši nosacījumi sūtījumu noformēšanai)")
    lines.append(
        "Note: the countries in this section are not necessarily "
        "suspended. Text only."
    )
    lines.append(
        f"Total items: {len(website_1_special)}"
    )

    for number, entry in enumerate(website_1_special, start=1):
        lines.append(f"{number}. LV: {entry['lv']}")
        lines.append(f"   EN: {entry['en']}")

    lines.append("")

    # --------------------------------------------------------
    # Website 2
    # --------------------------------------------------------

    lines.append("WEBSITE 2 — VALSTU SADALĪJUMS")
    lines.append(f"Source: {WEBSITE_2_URL}")
    lines.append(
        f"Total suspended countries: {len(website_2)}"
    )

    for country in website_2:
        lines.append(f"- {country['lv']} — {country['en']}")

    lines.append("")

    return "\n".join(lines)


# ============================================================
# Main
# ============================================================

def main():
    print("Starting Latvijas Pasts suspension monitor...")

    try:

        # Website 2 first: it supplies the Latvian -> English
        # country names that Website 1 needs.
        website_2, lv_to_en = check_website_2()

        website_1_suspended, website_1_special = (
            check_website_1(lv_to_en)
        )

        output = build_output(
            website_1_suspended,
            website_1_special,
            website_2,
        )

        OUTPUT_FILE.write_text(output, encoding="utf-8")

        print()
        print(f"Output written to {OUTPUT_FILE}")
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
