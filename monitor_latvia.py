#!/usr/bin/env python3

"""
Latvia Post international shipment suspension monitor.

Repository:
    latviapost

Output:
    output_latvia

The program monitors two Latvijas Pasts pages:

1. Current information about international shipment availability:
   https://pasts.lv/aizliegumi/aktuala-informacija-par-parrobezu-sutijumu-piegades-iespejam

2. Country breakdown:
   https://pasts.lv/aizliegumi/valstu-sadalijums

No Playwright, Selenium, Chrome, or other browser automation is used.

Website 1:
    Extracts countries from these two specific sections:

    - "Tuvo Austrumu galamērķi, uz kuriem nav iespējams nosūtīt sūtījumus:"
    - "Pasta sūtījumu piegāde uz nenoteiktu laiku nav pieejama uz
      sekojošiem galamērķiem:"

Website 2:
    A country is considered suspended when its country-detail page
    satisfies one of these conditions:

    - contains "vēstules nav iespējams nosūtīt"
    - contains:
      "Pašlaik uz šo galamērķi var nosūtīt tikai pakas,
       savukārt vēstules un sīkpakas nav iespējams nosūtīt."
    - has a top-level "Ņem vērā!" warning in the initial country
      status section

The program deliberately avoids adding a timestamp to output_latvia.
This is important because changedetection.io should detect real source
changes rather than a timestamp changing every day.
"""

# ============================================================================
# IMPORTS
# ============================================================================

from __future__ import annotations

import re
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET

from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


# ============================================================================
# CONFIGURATION
# ============================================================================

OUTPUT_FILE = "output_latvia"

WEBSITE_1_URL = (
    "https://pasts.lv/"
    "aizliegumi/aktuala-informacija-par-parrobezu-sutijumu-piegades-iespejam"
)

WEBSITE_2_URL = (
    "https://pasts.lv/aizliegumi/valstu-sadalijums"
)

PASTS_BASE_URL = "https://pasts.lv"

# English version of the country-detail area.
ENGLISH_COUNTRY_BASE = (
    "https://pasts.lv/en/prohibitions/country-breakdown/"
)

REQUEST_TIMEOUT = 30

# Keep requests polite. There are normally only a few hundred country pages.
REQUEST_DELAY_SECONDS = 0.15

USER_AGENT = (
    "Mozilla/5.0 "
    "(compatible; latviapost-monitor/1.0; "
    "+https://github.com/)"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "lv,en;q=0.8",
}

# Exact phrases requested by the user.
LETTER_SUSPENSION_PHRASE = (
    "vēstules nav iespējams nosūtīt"
)

PARCELS_ONLY_PHRASE = (
    "pašlaik uz šo galamērķi var nosūtīt tikai pakas, "
    "savukārt vēstules un sīkpakas nav iespējams nosūtīt."
)

TOP_WARNING_PHRASE = "ņem vērā!"

# Website 1 headings.
WEBSITE_1_HEADING_1 = (
    "tuvo austrumu galamērķi, uz kuriem nav iespējams nosūtīt sūtījumus:"
)

WEBSITE_1_HEADING_2 = (
    "pasta sūtījumu piegāde uz nenoteiktu laiku nav pieejama "
    "uz sekojošiem galamērķiem:"
)

# A few names on Website 1 are grammatically declined rather than being
# in nominative form. These are normalized for the final output.
LATVIAN_NAME_NORMALIZATION = {
    "jemenu": "Jemena",
    "jemenai": "Jemena",
    "jemenas": "Jemena",
    "sīriju": "Sīrija",
    "sīrijai": "Sīrija",
    "sīrijas": "Sīrija",
    "asv mazās aizjūras salām": "ASV Mazās aizjūras salas",
    "asv mazajām aizjūras salām": "ASV Mazās aizjūras salas",
}

# English fallback translations for names that are not necessarily
# represented as individual country pages.
ENGLISH_FALLBACKS = {
    "ASV Mazās aizjūras salas": "United States Minor Outlying Islands",
}


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass(frozen=True)
class Country:
    """
    Represents one suspended destination.
    """

    latvian: str
    english: str


# ============================================================================
# HTTP HELPERS
# ============================================================================

def create_session() -> requests.Session:
    """Create a requests session with sensible defaults."""

    session = requests.Session()
    session.headers.update(HEADERS)

    return session


def get_text(
    session: requests.Session,
    url: str,
) -> str:
    """
    Download a webpage and return its UTF-8 text.

    Raises an exception if the request fails or returns an HTTP error.
    """

    response = session.get(
        url,
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    # requests normally detects UTF-8 correctly, but explicitly using the
    # apparent encoding here avoids broken Latvian characters if the server
    # omits/incorrectly specifies the charset.
    if not response.encoding:
        response.encoding = response.apparent_encoding

    return response.text


# ============================================================================
# TEXT NORMALIZATION
# ============================================================================

def normalize_whitespace(text: str) -> str:
    """
    Collapse whitespace while preserving normal Unicode characters.
    """

    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def normalize_for_matching(text: str) -> str:
    """
    Normalize text for case-insensitive phrase matching.
    """

    text = normalize_whitespace(text)

    return text.casefold()


def normalize_latvian_name(name: str) -> str:
    """
    Convert a country/destination name to a clean nominative form where
    we know Latvijas Pasts currently uses a declined form.
    """

    name = normalize_whitespace(name)

    # Remove leading/trailing punctuation.
    name = name.strip(" \t\r\n.,;:–—-")

    lookup = name.casefold()

    if lookup in LATVIAN_NAME_NORMALIZATION:
        return LATVIAN_NAME_NORMALIZATION[lookup]

    return name


# ============================================================================
# COUNTRY LIST PARSING
# ============================================================================

def split_country_list(text: str) -> list[str]:
    """
    Split a comma-separated country/destination list.

    The current Website 1 format is a simple comma-separated list,
    e.g.:

        Irāna, Jemena un Sīrija.

    or:

        Jemenu, Sīriju, ASV Mazās aizjūras salām.

    Handles the final "un" as well.
    """

    text = normalize_whitespace(text)

    # Remove trailing sentence punctuation.
    text = text.rstrip(".;:")

    # Replace the final Latvian conjunction with a comma.
    text = re.sub(
        r"\s+un\s+",
        ", ",
        text,
        flags=re.IGNORECASE,
    )

    parts = []

    for item in text.split(","):
        item = normalize_latvian_name(item)

        if item:
            parts.append(item)

    return parts


def extract_list_after_heading(
    soup: BeautifulSoup,
    heading: str,
) -> list[str]:
    """
    Find the list item containing a Website 1 heading and extract the
    destination text following the heading.

    The page currently presents these pieces of information as bullet
    list items. The parser also has a text-based fallback so minor HTML
    layout changes are less likely to break it.
    """

    heading_normalized = normalize_for_matching(heading)

    # ----------------------------------------------------------------------
    # Preferred method: locate a <li>.
    # ----------------------------------------------------------------------

    for li in soup.find_all("li"):
        text = normalize_whitespace(li.get_text(" ", strip=True))
        text_normalized = normalize_for_matching(text)

        if heading_normalized in text_normalized:
            # Locate the original heading in the normalized text.
            start = text_normalized.find(heading_normalized)

            if start >= 0:
                remainder = text[start + len(heading):].strip()
                return split_country_list(remainder)

    # ----------------------------------------------------------------------
    # Fallback: use the entire visible page text.
    # ----------------------------------------------------------------------

    page_text = normalize_whitespace(
        soup.get_text(" ", strip=True)
    )

    page_text_normalized = normalize_for_matching(page_text)

    start = page_text_normalized.find(heading_normalized)

    if start < 0:
        raise RuntimeError(
            f"Could not find expected Website 1 heading:\n{heading}"
        )

    remainder = page_text[start + len(heading):]

    # Stop at the next known Website 1 bullet/section.
    stop_markers = [
        "Pārrobežu apdrošinātas sīkpakas iespējams",
        "Pasta sūtījumu piegāde Ukrainā",
        "Lai nokaidrotu",
        "Esi atbildīgs",
    ]

    stop_position = len(remainder)

    for marker in stop_markers:
        position = remainder.casefold().find(marker.casefold())

        if position >= 0:
            stop_position = min(stop_position, position)

    remainder = remainder[:stop_position]

    return split_country_list(remainder)


# ============================================================================
# WEBSITE 1
# ============================================================================

def scrape_website_1(
    session: requests.Session,
) -> list[Country]:
    """
    Scrape Website 1 according to the user's exact definition.

    The two lists are combined and duplicate countries are removed.
    """

    html = get_text(session, WEBSITE_1_URL)

    soup = BeautifulSoup(html, "html.parser")

    list_1 = extract_list_after_heading(
        soup,
        WEBSITE_1_HEADING_1,
    )

    list_2 = extract_list_after_heading(
        soup,
        WEBSITE_1_HEADING_2,
    )

    combined = []

    for name in list_1 + list_2:
        normalized = normalize_latvian_name(name)

        if normalized not in combined:
            combined.append(normalized)

    countries = []

    for name in combined:
        english = translate_destination(
            session=session,
            latvian_name=name,
            source_url=None,
        )

        countries.append(
            Country(
                latvian=name,
                english=english,
            )
        )

    return countries


# ============================================================================
# SITEMAP DISCOVERY
# ============================================================================

def local_name(tag: str) -> str:
    """
    Return an XML tag's local name without its namespace.
    """

    if "}" in tag:
        return tag.rsplit("}", 1)[1]

    return tag


def extract_sitemap_locations(xml_text: str) -> tuple[str, list[str]]:
    """
    Parse a sitemap or sitemap index.

    Returns:

        ("index", [child sitemap URLs])
    or
        ("urlset", [page URLs])
    """

    root = ET.fromstring(xml_text)

    root_type = local_name(root.tag).lower()

    locations = []

    for element in root.iter():
        if local_name(element.tag).lower() == "loc":
            if element.text:
                locations.append(element.text.strip())

    if root_type == "sitemapindex":
        return "index", locations

    if root_type == "urlset":
        return "urlset", locations

    # Some XML generators use an unexpected root name.
    # Infer the type from the children.
    for element in root:
        child_type = local_name(element.tag).lower()

        if child_type == "sitemap":
            return "index", locations

        if child_type == "url":
            return "urlset", locations

    raise RuntimeError(
        "Unrecognized sitemap XML format."
    )


def find_sitemap_urls(
    session: requests.Session,
) -> list[str]:
    """
    Find sitemap(s) using robots.txt and common sitemap locations.

    Supports both sitemap indexes and normal URL sitemaps.
    """

    candidates: list[str] = []

    # ----------------------------------------------------------------------
    # First try robots.txt because it is the authoritative location.
    # ----------------------------------------------------------------------

    try:
        robots_url = urljoin(
            PASTS_BASE_URL,
            "/robots.txt",
        )

        robots = get_text(
            session,
            robots_url,
        )

        for line in robots.splitlines():
            if line.lower().startswith("sitemap:"):
                sitemap_url = line.split(":", 1)[1].strip()

                if sitemap_url:
                    candidates.append(sitemap_url)

    except Exception:
        # Continue with common sitemap names.
        pass

    # ----------------------------------------------------------------------
    # Common sitemap names.
    # ----------------------------------------------------------------------

    candidates.extend(
        [
            urljoin(PASTS_BASE_URL, "/sitemap.xml"),
            urljoin(PASTS_BASE_URL, "/sitemap_index.xml"),
            urljoin(PASTS_BASE_URL, "/sitemap-index.xml"),
        ]
    )

    # Remove duplicates while preserving order.
    unique_candidates = []

    for url in candidates:
        if url not in unique_candidates:
            unique_candidates.append(url)

    # ----------------------------------------------------------------------
    # Recursively process sitemap indexes.
    # ----------------------------------------------------------------------

    visited_sitemaps: set[str] = set()
    page_urls: set[str] = set()

    def process_sitemap(url: str) -> None:
        if url in visited_sitemaps:
            return

        visited_sitemaps.add(url)

        try:
            xml_text = get_text(
                session,
                url,
            )
        except Exception:
            return

        try:
            sitemap_type, locations = extract_sitemap_locations(
                xml_text
            )
        except Exception:
            return

        if sitemap_type == "index":
            for child in locations:
                process_sitemap(child)

        else:
            for page_url in locations:
                page_urls.add(page_url)

    for sitemap in unique_candidates:
        process_sitemap(sitemap)

    if not page_urls:
        raise RuntimeError(
            "Could not discover any URLs from the Latvijas Pasts sitemap."
        )

    return sorted(page_urls)


# ============================================================================
# COUNTRY URL DISCOVERY
# ============================================================================

def is_latvian_country_page(url: str) -> bool:
    """
    Return True if the URL looks like a Latvian country-detail page.
    """

    parsed = urlparse(url)

    if parsed.netloc.lower() != "pasts.lv":
        return False

    path = parsed.path.rstrip("/")

    prefix = "/aizliegumi/valstu-sadalijums/"

    if not path.startswith(prefix):
        return False

    slug = path[len(prefix):]

    if not slug:
        return False

    # Only accept one path segment after country-breakdown.
    if "/" in slug:
        return False

    return True


def discover_country_urls(
    session: requests.Session,
) -> list[str]:
    """
    Discover all country-detail URLs.

    Primary method:
        sitemap

    Fallback:
        links found on the country breakdown page.
    """

    try:
        sitemap_urls = find_sitemap_urls(session)

        country_urls = [
            url
            for url in sitemap_urls
            if is_latvian_country_page(url)
        ]

        if country_urls:
            return sorted(set(country_urls))

    except Exception as exc:
        print(
            f"WARNING: sitemap discovery failed: {exc}",
            file=sys.stderr,
        )

    # ----------------------------------------------------------------------
    # Fallback to links exposed in the country breakdown page.
    # ----------------------------------------------------------------------

    html = get_text(
        session,
        WEBSITE_2_URL,
    )

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    country_urls = set()

    for anchor in soup.find_all("a", href=True):
        absolute = urljoin(
            WEBSITE_2_URL,
            anchor["href"],
        )

        if is_latvian_country_page(absolute):
            country_urls.add(absolute)

    if not country_urls:
        raise RuntimeError(
            "Could not discover any country-detail pages."
        )

    return sorted(country_urls)


# ============================================================================
# COUNTRY PAGE ANALYSIS
# ============================================================================

def get_country_title(
    soup: BeautifulSoup,
) -> str:
    """
    Get the country name from the page's H1.
    """

    h1 = soup.find("h1")

    if h1:
        title = normalize_whitespace(
            h1.get_text(" ", strip=True)
        )

        if title:
            return title

    raise RuntimeError(
        "Country page does not contain an H1 country name."
    )


def get_text_lines(
    soup: BeautifulSoup,
) -> list[str]:
    """
    Return visible text as normalized non-empty lines.
    """

    text = soup.get_text("\n", strip=True)

    lines = []

    for line in text.splitlines():
        line = normalize_whitespace(line)

        if line:
            lines.append(line)

    return lines


def has_top_warning(
    soup: BeautifulSoup,
) -> bool:
    """
    Detect a top-level "Ņem vērā!" status warning.

    We deliberately do NOT simply search the entire page for
    "Ņem vērā!", because normal countries can have informational
    warnings much further down the page. For example, the current
    USA page has "Ņem vērā!" messages concerning customs/service
    information, despite normal letter and parcel availability.

    Instead we inspect only the initial text immediately following
    the H1.
    """

    lines = get_text_lines(soup)

    h1 = soup.find("h1")

    if not h1:
        return False

    country_name = normalize_whitespace(
        h1.get_text(" ", strip=True)
    )

    # Find the H1 in the text-line representation.
    try:
        h1_index = next(
            i
            for i, line in enumerate(lines)
            if line == country_name
        )
    except StopIteration:
        return False

    # The current page layout puts the country status block immediately
    # after the H1. Inspect a deliberately small window.
    initial_lines = lines[
        h1_index + 1:
        h1_index + 12
    ]

    initial_text = normalize_for_matching(
        " ".join(initial_lines)
    )

    if TOP_WARNING_PHRASE not in initial_text:
        return False

    # A genuine suspension warning currently accompanies wording such as
    # "nav iespējams" or "tikai pakas". This prevents ordinary notices
    # elsewhere in a country page from being treated as suspensions.
    suspension_indicators = [
        "nav iespējams",
        "tikai pakas",
        "sūtījumus noformēt nav iespējams",
    ]

    return any(
        indicator in initial_text
        for indicator in suspension_indicators
    )


def analyze_country_page(
    soup: BeautifulSoup,
) -> tuple[bool, list[str]]:
    """
    Determine whether a country is suspended and return the reasons.
    """

    body_text = normalize_for_matching(
        soup.get_text(" ", strip=True)
    )

    reasons = []

    # ----------------------------------------------------------------------
    # Rule 1:
    # "vēstules nav iespējams nosūtīt"
    # ----------------------------------------------------------------------

    if LETTER_SUSPENSION_PHRASE in body_text:
        reasons.append(
            "vēstules nav iespējams nosūtīt"
        )

    # ----------------------------------------------------------------------
    # Rule 2:
    # "only parcels can currently be sent..."
    # ----------------------------------------------------------------------

    if PARCELS_ONLY_PHRASE in body_text:
        reasons.append(
            "tikai pakas iespējams nosūtīt"
        )

    # ----------------------------------------------------------------------
    # Rule 3:
    # top-level "Ņem vērā!" suspension warning
    # ----------------------------------------------------------------------

    if has_top_warning(soup):
        reasons.append(
            "top-level Ņem vērā! suspension warning"
        )

    return bool(reasons), reasons


# ============================================================================
# ENGLISH TRANSLATIONS
# ============================================================================

def find_english_country_url(
    soup: BeautifulSoup,
) -> str | None:
    """
    Find the English-language version of a country page.

    We first look for hreflang metadata, then for the visible
    "Angliski" language link.
    """

    # ----------------------------------------------------------------------
    # hreflang is preferable when available.
    # ----------------------------------------------------------------------

    for link in soup.find_all(
        "link",
        href=True,
    ):
        hreflang = link.get("hreflang", "")

        if str(hreflang).lower().startswith("en"):
            return urljoin(
                PASTS_BASE_URL,
                link["href"],
            )

    # ----------------------------------------------------------------------
    # Fallback: visible language link.
    # ----------------------------------------------------------------------

    for anchor in soup.find_all(
        "a",
        href=True,
    ):
        text = normalize_for_matching(
            anchor.get_text(" ", strip=True)
        )

        if text in {
            "angliski",
            "english",
            "english language",
        }:
            return urljoin(
                PASTS_BASE_URL,
                anchor["href"],
            )

    return None


def translate_destination(
    session: requests.Session,
    latvian_name: str,
    source_url: str | None,
) -> str:
    """
    Obtain the English destination name.

    For a country page, the English-language version of the same page
    is used. For Website 1 destinations that are territories/groupings
    without a dedicated country page, a built-in fallback is used.
    """

    normalized_name = normalize_latvian_name(
        latvian_name
    )

    # Known non-country/territory fallback.
    if normalized_name in ENGLISH_FALLBACKS:
        return ENGLISH_FALLBACKS[normalized_name]

    # If we have a country page, inspect its English-language link.
    if source_url:
        try:
            lv_html = get_text(
                session,
                source_url,
            )

            lv_soup = BeautifulSoup(
                lv_html,
                "html.parser",
            )

            english_url = find_english_country_url(
                lv_soup
            )

            if english_url:
                time.sleep(REQUEST_DELAY_SECONDS)

                english_html = get_text(
                    session,
                    english_url,
                )

                english_soup = BeautifulSoup(
                    english_html,
                    "html.parser",
                )

                h1 = english_soup.find("h1")

                if h1:
                    english_name = normalize_whitespace(
                        h1.get_text(" ", strip=True)
                    )

                    if english_name:
                        return english_name

        except Exception as exc:
            print(
                f"WARNING: Could not obtain English translation for "
                f"{normalized_name}: {exc}",
                file=sys.stderr,
            )

    # ----------------------------------------------------------------------
    # Last-resort fallback for Website 1 countries.
    #
    # These are only used if the English page cannot be reached.
    # ----------------------------------------------------------------------

    fallback = {
        "Irāna": "Iran",
        "Jemena": "Yemen",
        "Sīrija": "Syria",
    }

    if normalized_name in fallback:
        return fallback[normalized_name]

    # It is better to preserve the Latvian name than to silently invent
    # a translation.
    return normalized_name


# ============================================================================
# WEBSITE 2
# ============================================================================

def scrape_website_2(
    session: requests.Session,
) -> list[Country]:
    """
    Scrape all country-detail pages and apply the user's suspension rules.

    This replaces the need for browser automation and the literal
    "Rādīt visus" button click.
    """

    country_urls = discover_country_urls(
        session
    )

    print(
        f"Discovered {len(country_urls)} country pages.",
        file=sys.stderr,
    )

    suspended: list[Country] = []

    for index, url in enumerate(country_urls, start=1):
        print(
            f"Checking country {index}/{len(country_urls)}: {url}",
            file=sys.stderr,
        )

        try:
            html = get_text(
                session,
                url,
            )

            soup = BeautifulSoup(
                html,
                "html.parser",
            )

            latvian_name = get_country_title(
                soup
            )

            is_suspended, reasons = analyze_country_page(
                soup
            )

            if is_suspended:
                print(
                    f"  SUSPENDED: {latvian_name} "
                    f"({'; '.join(reasons)})",
                    file=sys.stderr,
                )

                english_name = translate_destination(
                    session=session,
                    latvian_name=latvian_name,
                    source_url=url,
                )

                suspended.append(
                    Country(
                        latvian=normalize_latvian_name(
                            latvian_name
                        ),
                        english=english_name,
                    )
                )

                time.sleep(REQUEST_DELAY_SECONDS)

        except Exception as exc:
            raise RuntimeError(
                f"Failed while processing country page:\n"
                f"{url}\n"
                f"Error: {exc}"
            ) from exc

        time.sleep(REQUEST_DELAY_SECONDS)

    # Remove duplicates by Latvian name.
    unique: dict[str, Country] = {}

    for country in suspended:
        unique[country.latvian] = country

    return list(unique.values())


# ============================================================================
# SORTING
# ============================================================================

def sort_countries(
    countries: Iterable[Country],
) -> list[Country]:
    """
    Sort countries deterministically by Latvian name.

    Unicode normalization helps keep accented Latvian characters stable.
    """

    def sort_key(country: Country) -> str:
        value = unicodedata.normalize(
            "NFKD",
            country.latvian,
        )

        return value.casefold()

    return sorted(
        countries,
        key=sort_key,
    )


# ============================================================================
# OUTPUT GENERATION
# ============================================================================

def format_country_list(
    countries: list[Country],
) -> str:
    """
    Format a country list for the output file.
    """

    countries = sort_countries(countries)

    lines = []

    for country in countries:
        lines.append(
            f"- {country.latvian} — {country.english}"
        )

    return "\n".join(lines)


def build_output(
    website_1: list[Country],
    website_2: list[Country],
) -> str:
    """
    Build the deterministic output_latvia contents.

    No timestamp is included intentionally.
    """

    website_1 = sort_countries(website_1)
    website_2 = sort_countries(website_2)

    lines = []

    lines.append(
        "LATVIJAS PASTS — SUSPENDED DESTINATIONS"
    )
    lines.append("")
    lines.append(
        "WEBSITE 1 — AKTUĀLĀ INFORMĀCIJA"
    )
    lines.append(
        "Source: "
        + WEBSITE_1_URL
    )
    lines.append("")
    lines.append(
        f"Total suspended destinations: {len(website_1)}"
    )
    lines.append("")

    if website_1:
        lines.extend(
            [
                f"- {country.latvian} — {country.english}"
                for country in website_1
            ]
        )
    else:
        lines.append("- NONE")

    lines.append("")
    lines.append(
        "WEBSITE 2 — VALSTU SADALĪJUMS"
    )
    lines.append(
        "Source: "
        + WEBSITE_2_URL
    )
    lines.append("")
    lines.append(
        f"Total suspended countries: {len(website_2)}"
    )
    lines.append("")

    if website_2:
        lines.extend(
            [
                f"- {country.latvian} — {country.english}"
                for country in website_2
            ]
        )
    else:
        lines.append("- NONE")

    lines.append("")

    return "\n".join(lines)


def write_output(
    content: str,
) -> None:
    """
    Write the output file using UTF-8.
    """

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8",
        newline="\n",
    ) as file:
        file.write(content)


# ============================================================================
# VALIDATION
# ============================================================================

def validate_results(
    website_1: list[Country],
    website_2: list[Country],
) -> None:
    """
    Fail rather than publishing a suspiciously empty result.

    This is important for changedetection.io: an outage or parsing failure
    should never silently overwrite a previously valid output file with
    an empty list.
    """

    if not website_1:
        raise RuntimeError(
            "Website 1 returned ZERO suspended destinations. "
            "This is suspicious because the parser may have broken or "
            "the source page structure may have changed."
        )

    # Website 2 could legitimately have zero suspended countries.
    # Therefore we do not require a non-empty result there.

    for country in website_1 + website_2:
        if not country.latvian.strip():
            raise RuntimeError(
                "A suspended destination has an empty Latvian name."
            )

        if not country.english.strip():
            raise RuntimeError(
                f"Missing English translation for "
                f"{country.latvian}."
            )


# ============================================================================
# MAIN
# ============================================================================

def main() -> int:
    """
    Main program entry point.
    """

    print(
        "Starting Latvijas Pasts suspension monitor...",
        file=sys.stderr,
    )

    session = create_session()

    # ----------------------------------------------------------------------
    # Website 1
    # ----------------------------------------------------------------------

    print(
        "Checking Website 1...",
        file=sys.stderr,
    )

    website_1 = scrape_website_1(
        session
    )

    print(
        f"Website 1 suspended destinations: {len(website_1)}",
        file=sys.stderr,
    )

    # ----------------------------------------------------------------------
    # Website 2
    # ----------------------------------------------------------------------

    print(
        "Checking Website 2...",
        file=sys.stderr,
    )

    website_2 = scrape_website_2(
        session
    )

    print(
        f"Website 2 suspended countries: {len(website_2)}",
        file=sys.stderr,
    )

    # ----------------------------------------------------------------------
    # Validate before touching the output file.
    # ----------------------------------------------------------------------

    validate_results(
        website_1=website_1,
        website_2=website_2,
    )

    # ----------------------------------------------------------------------
    # Generate deterministic output.
    # ----------------------------------------------------------------------

    output = build_output(
        website_1=website_1,
        website_2=website_2,
    )

    write_output(
        output
    )

    print(
        f"Successfully wrote {OUTPUT_FILE}",
        file=sys.stderr,
    )

    print(
        output
    )

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print(
            "Interrupted.",
            file=sys.stderr,
        )
        raise SystemExit(130)
    except Exception as exc:
        print(
            f"ERROR: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1)
