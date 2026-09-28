#!/usr/bin/env python3

"""
Latvijas Pasts suspension monitor.

WEBSITE 1
---------
Collects destinations from BOTH explicitly requested sections:

1. "Tuvo Austrumu galamērķi, uz kuriem nav iespējams
   nosūtīt sūtījumus:"

2. "Pasta sūtījumu piegāde uz nenoteiktu laiku nav pieejama
   uz sekojošiem galamērķiem:"

The two lists are combined and duplicates are removed.

WEBSITE 2
---------
Reads the embedded JavaScript COUNTRIES array and identifies
countries having a non-empty "warning" field.

No Playwright or browser automation is required.

OUTPUT
------
Creates:

    output_latvia

No timestamp is included so changedetection.io only detects
actual changes in the monitored data.
"""

# ============================================================
# IMPORTS
# ============================================================

import html
import json
import re
import sys
from typing import Any, Dict, List

import requests
from bs4 import BeautifulSoup


# ============================================================
# CONFIGURATION
# ============================================================

WEBSITE_1_URL = (
    "https://pasts.lv/aizliegumi/"
    "aktuala-informacija-par-parrobezu-sutijumu-piegades-iespejam"
)

WEBSITE_2_URL = (
    "https://pasts.lv/aizliegumi/valstu-sadalijums"
)

OUTPUT_FILE = "output_latvia"

REQUEST_TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept-Language": (
        "lv-LV,lv;q=0.9,en-US;q=0.8,en;q=0.7"
    ),
}


# ============================================================
# WEBSITE 1 — EXACT SECTION HEADINGS
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
# WEBSITE 1 — LATVIAN -> ENGLISH
# ============================================================

# These are the destinations currently used by Website 1.
#
# The parser recognizes both nominative and declined Latvian
# forms where necessary.
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
# HTTP
# ============================================================

def fetch_page(url: str) -> str:
    """
    Download a webpage.
    """

    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()

    except requests.RequestException as exc:
        raise RuntimeError(
            f"Could not download {url}: {exc}"
        ) from exc

    response.encoding = "utf-8"

    return response.text


# ============================================================
# TEXT NORMALIZATION
# ============================================================

def normalize_text(value: str) -> str:
    """
    Normalize whitespace and HTML entities.

    This also handles non-breaking spaces, which are important
    because the Latvijas Pasts page can contain them around the
    section headings.
    """

    value = html.unescape(value)

    # Convert NBSP and similar whitespace to ordinary spaces.
    value = value.replace("\xa0", " ")

    # Normalize all whitespace runs.
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def normalize_name(name: str) -> str:
    """
    Convert grammatical variants into one canonical Latvian name.
    """

    name = normalize_text(name)

    replacements = {
        "Irānu": "Irāna",
        "Jemenu": "Jemena",
        "Sīriju": "Sīrija",
    }

    return replacements.get(name, name)


# ============================================================
# WEBSITE 1 — DESTINATION DETECTION
# ============================================================

def find_section_text(
    soup: BeautifulSoup,
    heading: str,
) -> str:
    """
    Find one exact Website 1 heading and return the HTML/text
    belonging to that section.

    We deliberately search the rendered document text rather than
    depending on a particular CSS class or element type.

    The function looks for the exact heading and then takes the
    following document content until the next obvious section.
    """

    wanted = normalize_text(heading).lower()

    # --------------------------------------------------------
    # Get all visible text nodes in document order.
    # --------------------------------------------------------

    text_nodes = []

    for element in soup.find_all(
        ["p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6"]
    ):
        text = normalize_text(
            element.get_text(" ", strip=True)
        )

        if text:
            text_nodes.append(
                (
                    element,
                    text,
                )
            )

    # --------------------------------------------------------
    # Find the element containing the exact heading.
    # --------------------------------------------------------

    heading_index = None

    for index, (_, text) in enumerate(text_nodes):
        if text.lower() == wanted:
            heading_index = index
            break

    # --------------------------------------------------------
    # If exact matching failed, allow the heading to occur
    # within the element text.
    # --------------------------------------------------------

    if heading_index is None:
        for index, (_, text) in enumerate(text_nodes):
            if wanted in text.lower():
                heading_index = index
                break

    if heading_index is None:
        return ""

    # --------------------------------------------------------
    # Collect following text.
    #
    # We don't need to know the exact HTML structure. Instead,
    # we stop when another major heading appears.
    # --------------------------------------------------------

    collected = []

    for _, text in text_nodes[heading_index + 1:]:
        lower = text.lower()

        # Another major heading usually marks the next section.
        if (
            lower.startswith("tuvo ")
            or lower.startswith("pasta ")
            or lower.startswith("sūtījumu ")
            or lower.startswith("piegāde ")
            or lower.startswith("svarīgi")
            or lower.startswith("informācija")
        ):
            break

        collected.append(text)

        # The destination list is normally immediately after the
        # heading. We only need a limited amount of text.
        if len(collected) >= 10:
            break

    return " ".join(collected)


def find_section_in_raw_text(
    page_text: str,
    heading: str,
) -> str:
    """
    Fallback parser.

    Finds an exact heading in normalized page text and returns
    the text after it until the next known Website 1 heading.
    """

    text = normalize_text(page_text)

    wanted = normalize_text(heading)

    position = text.lower().find(
        wanted.lower()
    )

    if position == -1:
        return ""

    start = position + len(wanted)

    # Possible boundaries after the requested section.
    boundaries = [
        "Tuvo Austrumu galamērķi, uz kuriem nav iespējams",
        "Pasta sūtījumu piegāde uz nenoteiktu laiku nav pieejama",
        "Eiropas",
        "Āfrikas",
        "Amerikas",
        "Āzijas",
        "Austrālijas",
    ]

    end = len(text)

    for boundary in boundaries:
        boundary_position = text.lower().find(
            boundary.lower(),
            start,
        )

        if boundary_position != -1:
            end = min(
                end,
                boundary_position,
            )

    return text[start:end].strip()


def extract_destinations_from_text(
    section_text: str,
) -> List[str]:
    """
    Find known destinations inside one section.

    This is intentionally based on destination names rather than
    punctuation. Therefore a change from:

        Iran, Yemen and Syria

    to an HTML list such as:

        <li>Iran</li>
        <li>Yemen</li>
        <li>Syria</li>

    will still work.
    """

    section_text = normalize_text(section_text)

    if not section_text:
        return []

    found = []

    # Longest first so multi-word names are detected correctly.
    names = sorted(
        WEBSITE_1_NAME_MAP.keys(),
        key=len,
        reverse=True,
    )

    for name in names:
        # Case-insensitive substring matching.
        if name.lower() not in section_text.lower():
            continue

        canonical = normalize_name(name)

        if canonical not in found:
            found.append(canonical)

    return found


def parse_website_1(
    html_text: str,
) -> List[Dict[str, str]]:
    """
    Parse BOTH requested Website 1 sections.

    The result is deduplicated across the two sections.
    """

    soup = BeautifulSoup(
        html_text,
        "html.parser",
    )

    # --------------------------------------------------------
    # Make a copy for visible text extraction.
    # --------------------------------------------------------

    for element in soup(
        ["script", "style", "noscript"]
    ):
        element.decompose()

    # --------------------------------------------------------
    # SECTION 1
    # --------------------------------------------------------

    section_1_text = find_section_text(
        soup,
        SECTION_1_HEADING,
    )

    # If HTML structure did not expose it correctly, use raw
    # page-text fallback.
    if not section_1_text:
        section_1_text = find_section_in_raw_text(
            soup.get_text(" ", strip=True),
            SECTION_1_HEADING,
        )

    section_1_destinations = (
        extract_destinations_from_text(
            section_1_text
        )
    )

    # --------------------------------------------------------
    # SECTION 2
    # --------------------------------------------------------

    section_2_text = find_section_text(
        soup,
        SECTION_2_HEADING,
    )

    # Fallback if necessary.
    if not section_2_text:
        section_2_text = find_section_in_raw_text(
            soup.get_text(" ", strip=True),
            SECTION_2_HEADING,
        )

    section_2_destinations = (
        extract_destinations_from_text(
            section_2_text
        )
    )

    # --------------------------------------------------------
    # DIAGNOSTIC OUTPUT
    # --------------------------------------------------------

    print(
        "Website 1 — first section:"
    )
    print(
        f"  {section_1_destinations}"
    )

    print(
        "Website 1 — last section:"
    )
    print(
        f"  {section_2_destinations}"
    )

    # --------------------------------------------------------
    # COMBINE BOTH LISTS
    # --------------------------------------------------------

    combined_names = []

    for name in (
        section_1_destinations
        + section_2_destinations
    ):
        if name not in combined_names:
            combined_names.append(name)

    # --------------------------------------------------------
    # Convert to Latvian + English.
    # --------------------------------------------------------

    result = []

    for name_lv in combined_names:
        name_en = WEBSITE_1_NAME_MAP.get(
            name_lv
        )

        if not name_en:
            print(
                "WARNING: No English name mapping for "
                f"{name_lv}",
                file=sys.stderr,
            )
            continue

        result.append(
            {
                "nameLv": name_lv,
                "nameEn": name_en,
            }
        )

    # Deterministic order.
    result.sort(
        key=lambda item: (
            item["nameLv"].lower(),
            item["nameEn"].lower(),
        )
    )

    return result


# ============================================================
# WEBSITE 2 — COUNTRIES ARRAY
# ============================================================

def extract_countries_array(
    html_text: str,
) -> List[Dict[str, Any]]:
    """
    Extract the embedded JavaScript COUNTRIES array.
    """

    # Primary declaration.
    pattern = re.compile(
        r"\bvar\s+COUNTRIES\s*=\s*(\[[\s\S]*?\])\s*;",
        re.MULTILINE,
    )

    match = pattern.search(
        html_text
    )

    # Future-proofing if declaration changes to const/let.
    if not match:
        pattern = re.compile(
            r"\b(?:const|let)\s+COUNTRIES\s*=\s*"
            r"(\[[\s\S]*?\])\s*;",
            re.MULTILINE,
        )

        match = pattern.search(
            html_text
        )

    if not match:
        raise RuntimeError(
            "Could not find the COUNTRIES JavaScript array "
            "on Website 2."
        )

    array_text = match.group(1)

    try:
        countries = json.loads(
            array_text
        )

    except json.JSONDecodeError as exc:
        start = max(
            0,
            exc.pos - 150,
        )

        end = min(
            len(array_text),
            exc.pos + 150,
        )

        context = array_text[start:end]

        raise RuntimeError(
            "Found COUNTRIES but could not parse it as JSON.\n"
            f"Parser error: {exc}\n"
            f"Context:\n{context}"
        ) from exc

    if not isinstance(
        countries,
        list,
    ):
        raise RuntimeError(
            "COUNTRIES was found but is not a list."
        )

    return countries


# ============================================================
# WEBSITE 2 — SUSPENSION RULE
# ============================================================

def country_is_suspended(
    country: Dict[str, Any],
) -> bool:
    """
    Website 2 uses country.warning to generate its visible
    "Ņem vērā!" warning.

    Any non-empty warning therefore represents one of the
    special destination restrictions we want to monitor.

    We do NOT use letters == "Nē" by itself.
    """

    warning = country.get(
        "warning"
    )

    if warning is None:
        return False

    if not isinstance(
        warning,
        str,
    ):
        return bool(warning)

    return bool(
        warning.strip()
    )


def parse_website_2(
    html_text: str,
) -> List[Dict[str, str]]:
    """
    Return all Website 2 countries having a warning.
    """

    countries = extract_countries_array(
        html_text
    )

    suspended = []

    for country in countries:
        if not isinstance(
            country,
            dict,
        ):
            continue

        if not country_is_suspended(
            country
        ):
            continue

        name_lv = str(
            country.get(
                "nameLv",
                "",
            )
        ).strip()

        name_en = str(
            country.get(
                "nameEn",
                "",
            )
        ).strip()

        code = str(
            country.get(
                "code",
                "",
            )
        ).strip()

        warning = str(
            country.get(
                "warning",
                "",
            )
        ).strip()

        if not name_lv or not name_en:
            continue

        suspended.append(
            {
                "code": code,
                "nameLv": name_lv,
                "nameEn": name_en,
                "warning": warning,
            }
        )

    # Deterministic ordering.
    suspended.sort(
        key=lambda item: (
            item["nameLv"].lower(),
            item["nameEn"].lower(),
        )
    )

    return suspended


# ============================================================
# OUTPUT
# ============================================================

def make_output(
    website_1: List[Dict[str, str]],
    website_2: List[Dict[str, str]],
) -> str:
    """
    Create deterministic output_latvia contents.
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

    lines.append("")

    lines.append(
        f"Total suspended destinations: "
        f"{len(website_1)}"
    )

    lines.append("")

    for item in website_1:
        lines.append(
            f"- {item['nameLv']} — {item['nameEn']}"
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

    lines.append("")

    lines.append(
        f"Total suspended countries: "
        f"{len(website_2)}"
    )

    lines.append("")

    for item in website_2:
        lines.append(
            f"- {item['nameLv']} — {item['nameEn']}"
        )

    lines.append("")

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================

def main() -> int:
    """
    Run Website 1 and Website 2 checks.
    """

    print(
        "Starting Latvijas Pasts suspension monitor..."
    )

    # ========================================================
    # WEBSITE 1
    # ========================================================

    print(
        "Checking Website 1..."
    )

    try:
        website_1_html = fetch_page(
            WEBSITE_1_URL
        )

        website_1 = parse_website_1(
            website_1_html
        )

    except Exception as exc:
        print(
            "ERROR: Website 1 check failed: "
            f"{exc}",
            file=sys.stderr,
        )
        return 1

    print(
        "Website 1 suspended destinations: "
        f"{len(website_1)}"
    )

    # ========================================================
    # WEBSITE 2
    # ========================================================

    print(
        "Checking Website 2..."
    )

    try:
        website_2_html = fetch_page(
            WEBSITE_2_URL
        )

        website_2 = parse_website_2(
            website_2_html
        )

    except Exception as exc:
        print(
            "ERROR: Website 2 check failed: "
            f"{exc}",
            file=sys.stderr,
        )
        return 1

    print(
        "Website 2 suspended countries: "
        f"{len(website_2)}"
    )

    # ========================================================
    # WRITE OUTPUT
    # ========================================================

    output = make_output(
        website_1,
        website_2,
    )

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8",
        newline="\n",
    ) as file:
        file.write(
            output
        )

    # ========================================================
    # DISPLAY OUTPUT
    # ========================================================

    print("")
    print(
        "Generated output_latvia:"
    )
    print(
        "----------------------------------------"
    )
    print(
        output,
        end="",
    )
    print(
        "----------------------------------------"
    )

    return 0


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    sys.exit(
        main()
    )
