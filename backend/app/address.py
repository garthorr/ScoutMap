"""Address normalization and matching utilities.

Provides a pluggable normalizer so new public datasets can reuse the same
matching pipeline.
"""

import re

# Common directional and suffix abbreviations
_DIRECTIONALS = {
    "north": "N", "south": "S", "east": "E", "west": "W",
    "northeast": "NE", "northwest": "NW", "southeast": "SE", "southwest": "SW",
    "n": "N", "s": "S", "e": "E", "w": "W",
    "ne": "NE", "nw": "NW", "se": "SE", "sw": "SW",
}

_SUFFIXES = {
    "street": "ST", "st": "ST",
    "avenue": "AVE", "ave": "AVE", "av": "AVE",
    "boulevard": "BLVD", "blvd": "BLVD",
    "drive": "DR", "dr": "DR",
    "lane": "LN", "ln": "LN",
    "road": "RD", "rd": "RD",
    "court": "CT", "ct": "CT",
    "circle": "CIR", "cir": "CIR",
    "place": "PL", "pl": "PL",
    "way": "WAY",
    "trail": "TRL", "trl": "TRL",
    "parkway": "PKWY", "pkwy": "PKWY",
    "highway": "HWY", "hwy": "HWY",
    "terrace": "TER", "ter": "TER",
    "loop": "LOOP",
    "crossing": "XING", "xing": "XING",
}

_UNIT_PREFIXES = {"apt", "suite", "ste", "unit", "#", "no"}


def normalize_address(raw: str) -> str:
    """Return a canonical form of *raw* for dedup / matching.

    Rules applied:
    1. Upper-case, strip extra whitespace and punctuation.
    2. Replace directional words with abbreviations.
    3. Replace street-type suffixes with USPS abbreviations.
    4. Strip unit/apt designators (kept separately in the model).
    """
    if not raw:
        return ""

    text = raw.upper().strip()
    # Remove periods, commas, hashes (but keep # as part of processing)
    text = re.sub(r"[.,]", "", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)

    tokens = text.split()
    result = []
    skip_next = False

    for i, tok in enumerate(tokens):
        if skip_next:
            skip_next = False
            continue

        low = tok.lower()

        # Strip unit prefix + value
        if low.rstrip(".") in _UNIT_PREFIXES:
            skip_next = True  # skip the unit number too
            continue
        if low.startswith("#"):
            continue

        # Directionals
        if low in _DIRECTIONALS:
            result.append(_DIRECTIONALS[low])
            continue

        # Suffixes
        if low in _SUFFIXES:
            result.append(_SUFFIXES[low])
            continue

        result.append(tok)

    return " ".join(result)


def addresses_match(a: str, b: str) -> bool:
    """Check if two raw addresses match after normalization."""
    return normalize_address(a) == normalize_address(b)


def parse_address_parts(full: str) -> dict:
    """Best-effort parse of a full address string into components."""
    parts = {"address_number": "", "street_name": "", "unit": "",
             "city": "", "state": "", "zip_code": ""}
    if not full:
        return parts

    text = full.strip()
    # Try to split off city/state/zip
    # Pattern: ..., City, ST ZIP
    csz = re.search(r",\s*([^,]+),\s*([A-Za-z]{2})\s+(\d{5}(?:-\d{4})?)$", text)
    street_part = text
    if csz:
        parts["city"] = csz.group(1).strip()
        parts["state"] = csz.group(2).upper()
        parts["zip_code"] = csz.group(3)
        street_part = text[:csz.start()].strip()

    tokens = street_part.split()
    if tokens and re.match(r"^\d+", tokens[0]):
        parts["address_number"] = tokens[0]
        parts["street_name"] = " ".join(tokens[1:])
    else:
        parts["street_name"] = street_part

    return parts
