#!/usr/bin/env python3

"""
Latvijas Pasts suspension monitor.

Checks:

1. Latvijas Pasts "Aktuālā informācija par pārrobežu sūtījumu
   piegādes iespējām" page.

2. Latvijas Pasts "Valstu sadalījums" page.

Website 2 does NOT use browser automation.

The country information is embedded directly in the page JavaScript
as:

    var COUNTRIES = [
        ...
    ];

We extract that array and inspect each country's "warning" field.

Output:

    output_latvia

The output is deliberately deterministic and contains no timestamp,
so changedetection.io only detects actual data changes.
"""

# ============================================================
# IMPORTS
# ============================================================

import html
import json
import re
import sys
from typing import Any, Dict, List, Optional

import requests
from bs4 import BeautifulSoup


# ============================================================
# CONFIGURATION
# ============================================================

WEBSITE_1_URL = (
    "https://pasts.lv/aizliegumi/"
    "aktuala-informacija-par-parrobezu-sutijumu-piegades-iespejam"
    "#aktuala-informacija-par-parrobezu-sutijumu-piegades-iespejam"
)

WEBSITE_2_URL = (
    "https://pasts.lv/aizliegumi/valstu-sadalijums"
    "?q=pale&r=0#valstu-sadalijums"
)

OUTPUT_FILE = "output_latvia"

REQUEST_TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept-Language": "lv-LV,lv;q=0.9,en-US;q=0.8,en;q=0.7",
}


# ============================================================
# HTTP HELPERS
# ============================================================

def fetch_page(url: str) -> str:
    """
    Download a webpage and return its HTML.

    Raises RuntimeError on HTTP or connection errors.
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

    # requests normally detects UTF-8 correctly, but Latvijas Pasts
    # pages are UTF-8 and this keeps the text handling explicit.
    response.encoding = response.apparent_encoding or response.encoding

    return response.text


# ============================================================
# WEBSITE 1
# ============================================================

def clean_text(value: str) -> str:
    """
    Normalize whitespace in extracted text.
    """

    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def normalize_destination_name(name: str) -> str:
    """
    Convert declined Latvian destination wording to a common
    display form where known.

    Website 1 can mention destinations in grammatical forms such as:

        Jemenu
        Sīriju

    while we want consistent output:

        Jemena
        Sīrija
    """

    replacements = {
        "Jemenu": "Jemena",
        "Sīriju": "Sīrija",
        "Irānu": "Irāna",
    }

    return replacements.get(name.strip(), name.strip())


def parse_website_1(html_text: str) -> List[Dict[str, str]]:
    """
    Extract suspended destinations from Website 1.

    The two relevant sections are:

        Tuvo Austrumu galamērķi, uz kuriem nav iespējams
        nosūtīt sūtījumus:

    and:

        Pasta sūtījumu piegāde uz nenoteiktu laiku nav
        pieejama uz sekojošiem galamērķiem:

    The result is deduplicated.
    """

    soup = BeautifulSoup(html_text, "html.parser")

    # Remove elements that can interfere with text extraction.
    for element in soup(["script", "style", "noscript"]):
        element.decompose()

    # Work with the visible page text.
    page_text = soup.get_text("\n", strip=True)
    page_text = html.unescape(page_text)

    destinations: List[str] = []

    # --------------------------------------------------------
    # Section 1
    # --------------------------------------------------------

    section_1_marker = (
        "Tuvo Austrumu galamērķi, uz kuriem nav iespējams "
        "nosūtīt sūtījumus:"
    )

    section_1_pos = page_text.find(section_1_marker)

    if section_1_pos != -1:
        after = page_text[
            section_1_pos + len(section_1_marker):
        ]

        # Take only a small section after the heading.
        # The current page places the relevant destinations
        # immediately after the heading.
        section = after[:1000]

        # Look for the first sentence-like destination list.
        match = re.search(
            r"([A-ZĀČĒĢĪĶĻŅŠŪŽ][^.!?\n]{1,250})",
            section,
        )

        if match:
            candidate = clean_text(match.group(1))

            # Remove unrelated trailing text if it was included.
            candidate = re.split(
                r"\b(?:Līdz|Sūtījumu|Informācija|"
                r"Papildu|Plašāka)\b",
                candidate,
                maxsplit=1,
            )[0].strip()

            if candidate:
                # Split common list separators.
                parts = re.split(
                    r"\s*,\s*|\s+un\s+",
                    candidate,
                )

                for part in parts:
                    part = normalize_destination_name(
                        clean_text(part)
                    )
                    if part:
                        destinations.append(part)

    # --------------------------------------------------------
    # Section 2
    # --------------------------------------------------------

    section_2_marker = (
        "Pasta sūtījumu piegāde uz nenoteiktu laiku nav "
        "pieejama uz sekojošiem galamērķiem:"
    )

    section_2_pos = page_text.find(section_2_marker)

    if section_2_pos != -1:
        after = page_text[
            section_2_pos + len(section_2_marker):
        ]

        section = after[:1500]

        # Extract the first useful text block.
        lines = [
            clean_text(line)
            for line in section.splitlines()
            if clean_text(line)
        ]

        for line in lines:
            # Ignore obvious headings/navigation.
            if len(line) > 250:
                continue

            # A destination line generally contains either commas,
            # "un", or known country wording.
            if (
                "," in line
                or " un " in line
                or "ASV" in line
            ):
                parts = re.split(
                    r"\s*,\s*|\s+un\s+",
                    line,
                )

                for part in parts:
                    part = normalize_destination_name(
                        clean_text(part)
                    )

                    if part:
                        destinations.append(part)

                break

    # --------------------------------------------------------
    # Fallback: parse the known section headings structurally
    # --------------------------------------------------------

    if not destinations:
        # Search HTML itself around the section headings.
        text = soup.get_text(" ", strip=True)

        patterns = [
            r"Tuvo Austrumu galamērķi.*?:\s*([^.;]+)",
            r"Pasta sūtījumu piegāde uz nenoteiktu laiku.*?:\s*([^.;]+)",
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if not match:
                continue

            candidate = clean_text(match.group(1))

            parts = re.split(
                r"\s*,\s*|\s+un\s+",
                candidate,
            )

            for part in parts:
                part = normalize_destination_name(part)
                if part:
                    destinations.append(part)

    # --------------------------------------------------------
    # Map Latvian names to English names.
    # --------------------------------------------------------

    lv_to_en = {
        "Irāna": "Iran",
        "Jemena": "Yemen",
        "Sīrija": "Syria",
        "ASV Mazās aizjūras salas":
            "United States Minor Outlying Islands",
    }

    result: List[Dict[str, str]] = []
    seen = set()

    for lv_name in destinations:
        lv_name = normalize_destination_name(lv_name)

        # Only retain destinations for which we can confidently
        # identify the English name.
        en_name = lv_to_en.get(lv_name)

        if not en_name:
            continue

        key = lv_name.lower()

        if key in seen:
            continue

        seen.add(key)

        result.append(
            {
                "nameLv": lv_name,
                "nameEn": en_name,
            }
        )

    result.sort(
        key=lambda item: (
            item["nameLv"].lower(),
            item["nameEn"].lower(),
        )
    )

    return result


# ============================================================
# WEBSITE 2 — COUNTRIES EXTRACTION
# ============================================================

def extract_countries_array(html_text: str) -> List[Dict[str, Any]]:
    """
    Extract the JavaScript COUNTRIES array from Website 2.

    The page contains data structured like:

        var COUNTRIES = [
          {
            "code": "AF",
            ...
          }
        ];

    Because the contents shown by the site are JSON-compatible,
    we can extract the array without executing JavaScript.
    """

    # --------------------------------------------------------
    # Primary pattern
    # --------------------------------------------------------

    pattern = re.compile(
        r"\bvar\s+COUNTRIES\s*=\s*(\[[\s\S]*?\])\s*;",
        re.MULTILINE,
    )

    match = pattern.search(html_text)

    if not match:
        # Support const/let too, in case the site changes the
        # declaration type later.
        pattern = re.compile(
            r"\b(?:const|let)\s+COUNTRIES\s*=\s*(\[[\s\S]*?\])\s*;",
            re.MULTILINE,
        )
        match = pattern.search(html_text)

    if not match:
        raise RuntimeError(
            "Could not find the COUNTRIES JavaScript array "
            "on Website 2."
        )

    array_text = match.group(1)

    # --------------------------------------------------------
    # Parse as JSON
    # --------------------------------------------------------

    try:
        countries = json.loads(array_text)
    except json.JSONDecodeError as exc:
        # Provide useful diagnostics if Latvijas Pasts changes
        # the embedded structure.
        start = max(0, exc.pos - 150)
        end = min(len(array_text), exc.pos + 150)

        context = array_text[start:end]

        raise RuntimeError(
            "Found COUNTRIES but could not parse it as JSON.\n"
            f"Parser error: {exc}\n"
            f"Context:\n{context}"
        ) from exc

    if not isinstance(countries, list):
        raise RuntimeError(
            "COUNTRIES was found but is not a list."
        )

    return countries


# ============================================================
# WEBSITE 2 — SUSPENSION DETECTION
# ============================================================

def country_is_suspended(country: Dict[str, Any]) -> bool:
    """
    Determine whether a country should appear in Website 2's
    suspended-destination output.

    The site's country popup renders:

        warningHtml(country.warning)

    and warningHtml() creates the visible:

        Ņem vērā!

    warning box.

    Therefore a non-empty "warning" field is the authoritative
    indicator for the exceptional destination restrictions we
    need to monitor.

    Examples from the site data include:

        "Pašlaik uz šo galamērķi var nosūtīt tikai pakas,
         savukārt vēstules un sīkpakas nav iespējams nosūtīt."

    and:

        "Pašlaik uz šo galamērķi sūtījumus noformēt nav iespējams."

    We deliberately do NOT classify a country as fully suspended
    merely because country["letters"] == "Nē", because the site
    can have individual service restrictions that do not mean
    the entire destination is suspended.
    """

    warning = country.get("warning")

    if warning is None:
        return False

    if not isinstance(warning, str):
        return bool(warning)

    return bool(warning.strip())


def parse_website_2(html_text: str) -> List[Dict[str, str]]:
    """
    Extract all countries with a non-empty warning field.
    """

    countries = extract_countries_array(html_text)

    suspended: List[Dict[str, str]] = []

    for country in countries:
        if not isinstance(country, dict):
            continue

        if not country_is_suspended(country):
            continue

        name_lv = str(
            country.get("nameLv", "")
        ).strip()

        name_en = str(
            country.get("nameEn", "")
        ).strip()

        code = str(
            country.get("code", "")
        ).strip()

        warning = str(
            country.get("warning", "")
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
    Build deterministic output for changedetection.io.
    """

    lines: List[str] = []

    lines.append(
        "LATVIJAS PASTS — SUSPENDED DESTINATIONS"
    )
    lines.append("")
    lines.append(
        "WEBSITE 1 — AKTUĀLĀ INFORMĀCIJA"
    )
    lines.append(
        f"Source: {WEBSITE_1_URL.split('#')[0]}"
    )
    lines.append("")
    lines.append(
        f"Total suspended destinations: {len(website_1)}"
    )
    lines.append("")

    for item in website_1:
        lines.append(
            f"- {item['nameLv']} — {item['nameEn']}"
        )

    lines.append("")
    lines.append(
        "WEBSITE 2 — VALSTU SADALĪJUMS"
    )
    lines.append(
        f"Source: {WEBSITE_2_URL.split('?')[0]}"
    )
    lines.append("")
    lines.append(
        f"Total suspended countries: {len(website_2)}"
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
    print(
        "Starting Latvijas Pasts suspension monitor..."
    )

    # --------------------------------------------------------
    # Website 1
    # --------------------------------------------------------

    print("Checking Website 1...")

    try:
        website_1_html = fetch_page(
            WEBSITE_1_URL.split("#")[0]
        )

        website_1 = parse_website_1(
            website_1_html
        )

    except Exception as exc:
        print(
            f"ERROR: Website 1 check failed: {exc}",
            file=sys.stderr,
        )
        return 1

    print(
        f"Website 1 suspended destinations: "
        f"{len(website_1)}"
    )

    # --------------------------------------------------------
    # Website 2
    # --------------------------------------------------------

    print("Checking Website 2...")

    try:
        website_2_html = fetch_page(
            WEBSITE_2_URL.split("?")[0]
        )

        website_2 = parse_website_2(
            website_2_html
        )

    except Exception as exc:
        print(
            f"ERROR: Website 2 check failed: {exc}",
            file=sys.stderr,
        )
        return 1

    print(
        f"Website 2 suspended countries: "
        f"{len(website_2)}"
    )

    # --------------------------------------------------------
    # Write output
    # --------------------------------------------------------

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
        file.write(output)

    # --------------------------------------------------------
    # Display output in GitHub Actions log
    # --------------------------------------------------------

    print("")
    print("Generated output_latvia:")
    print("----------------------------------------")
    print(output, end="")
    print("----------------------------------------")

    return 0


if __name__ == "__main__":
    sys.exit(main())
