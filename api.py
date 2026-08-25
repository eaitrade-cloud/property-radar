import json
import os
import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS


app = Flask(__name__)
CORS(app)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATA_FILE = os.path.join(
    BASE_DIR,
    "data",
    "market_benchmarks.json"
)

REQUEST_TIMEOUT = 15

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/138.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en;q=0.9,fr;q=0.8"
}


# ============================================================
# ROUTES
# ============================================================

@app.route("/", methods=["GET"])
def home():
    return send_from_directory(
        BASE_DIR,
        "index.html"
    )


@app.route("/api/status", methods=["GET"])
def status():
    return jsonify({
        "status": "ok",
        "service": "Property Radar API",
        "version": "2.2"
    })


# ============================================================
# GENERAL HELPERS
# ============================================================

def clean_text(value):
    return re.sub(
        r"\s+",
        " ",
        str(value or "")
    ).strip()


def normalise_text(value):
    text = clean_text(value).lower()

    replacements = {
        "’": "'",
        "‘": "'",
        "`": "'",
        "â€™": "'",
        "amezmiz": "amizmiz",
        "marrakesh": "marrakech",
        "tanger": "tangier",
        "fez": "fes"
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    return text


def safe_int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def numeric_value(value):
    if value is None:
        return None

    text = str(value)
    text = text.replace("\xa0", " ")
    text = text.replace(" ", " ")
    text = text.replace(" ", " ")

    match = re.search(
        r"\d[\d\s.,]*",
        text
    )

    if not match:
        return None

    number = re.sub(
        r"[^\d]",
        "",
        match.group(0)
    )

    if not number:
        return None

    try:
        return int(number)
    except ValueError:
        return None


def unique_numbers(values):
    result = []

    for value in values:
        if value is None:
            continue

        if value not in result:
            result.append(value)

    return result


# ============================================================
# JSON-LD
# ============================================================

def get_json_ld(soup):
    results = []

    scripts = soup.find_all(
        "script",
        attrs={
            "type": "application/ld+json"
        }
    )

    for script in scripts:
        raw = script.string

        if not raw:
            raw = script.get_text(
                strip=True
            )

        if not raw:
            continue

        try:
            data = json.loads(raw)

            if isinstance(data, list):
                results.extend(data)

            elif isinstance(data, dict):
                graph = data.get("@graph")

                if isinstance(graph, list):
                    results.extend(graph)

                results.append(data)

        except Exception:
            continue

    return results


def walk_json(value):
    if isinstance(value, dict):
        yield value

        for child in value.values():
            yield from walk_json(child)

    elif isinstance(value, list):
        for child in value:
            yield from walk_json(child)


# ============================================================
# TITLE / DESCRIPTION
# ============================================================

def extract_title(soup):
    og = soup.find(
        "meta",
        attrs={"property": "og:title"}
    )

    if og:
        value = clean_text(
            og.get("content")
        )

        if value:
            return value

    if soup.title:
        return clean_text(
            soup.title.get_text(
                " ",
                strip=True
            )
        )

    return ""


def extract_description(soup):
    selectors = [
        {"name": "description"},
        {"property": "og:description"}
    ]

    for selector in selectors:
        tag = soup.find(
            "meta",
            attrs=selector
        )

        if tag:
            value = clean_text(
                tag.get("content")
            )

            if value:
                return value

    return ""


# ============================================================
# PRICE ENGINE
# ============================================================

def valid_price(value):
    return (
        value is not None
        and 100000 <= value <= 200000000
    )


def add_price_candidate(
    candidates,
    value,
    source,
    score
):
    number = numeric_value(value)

    if not valid_price(number):
        return

    candidates.append({
        "value": number,
        "source": source,
        "score": score
    })


def collect_json_prices(
    json_objects,
    candidates
):
    for root in json_objects:
        for item in walk_json(root):

            item_type = normalise_text(
                item.get("@type", "")
            )

            high_priority = any(
                word in item_type
                for word in [
                    "product",
                    "offer",
                    "residence",
                    "house",
                    "apartment"
                ]
            )

            score = (
                100
                if high_priority
                else 82
            )

            for key in [
                "price",
                "lowPrice",
                "highPrice"
            ]:
                if key in item:
                    add_price_candidate(
                        candidates,
                        item.get(key),
                        "json_ld_" + key,
                        score
                    )

            offers = item.get(
                "offers"
            )

            if isinstance(offers, dict):
                add_price_candidate(
                    candidates,
                    offers.get("price"),
                    "json_ld_offer",
                    105
                )


def collect_meta_prices(
    soup,
    candidates
):
    selectors = [
        (
            {"property": "product:price:amount"},
            100,
            "product_meta"
        ),
        (
            {"property": "og:price:amount"},
            98,
            "og_price"
        ),
        (
            {"itemprop": "price"},
            95,
            "itemprop_price"
        ),
        (
            {"name": "price"},
            90,
            "price_meta"
        )
    ]

    for attrs, score, source in selectors:
        tags = soup.find_all(
            "meta",
            attrs=attrs
        )

        for tag in tags:
            add_price_candidate(
                candidates,
                tag.get("content"),
                source,
                score
            )


def collect_description_prices(
    description,
    candidates
):
    patterns = [
        r"(?:price|prix|asking price)"
        r"\s*[:\-]?\s*"
        r"(\d[\d\s.,]+)"
        r"\s*(?:MAD|DH|DHS)",

        r"(\d[\d\s.,]+)"
        r"\s*(?:MAD|DH|DHS)"
    ]

    for pattern in patterns:
        for match in re.findall(
            pattern,
            description,
            re.IGNORECASE
        ):
            add_price_candidate(
                candidates,
                match,
                "description",
                75
            )


def collect_page_prices(
    page_text,
    candidates
):
    patterns = [
        r"(?:price|prix|asking price)"
        r"\s*[:\-]?\s*"
        r"(\d[\d\s.,]+)"
        r"\s*(?:MAD|DH|DHS)",

        r"(\d[\d\s.,]+)"
        r"\s*(?:MAD|DH|DHS)"
    ]

    for pattern in patterns:
        matches = re.findall(
            pattern,
            page_text,
            re.IGNORECASE
        )

        for match in matches[:20]:
            add_price_candidate(
                candidates,
                match,
                "page_text",
                40
            )


def candidate_support(
    candidate,
    candidates
):
    value = candidate["value"]
    support = 0

    for other in candidates:
        if other is candidate:
            continue

        difference = abs(
            other["value"] - value
        ) / value

        if difference <= 0.01:
            support += 15

        elif difference <= 0.05:
            support += 5

    return support


def select_price(candidates):
    if not candidates:
        return None, None, [], False

    scored = []

    for candidate in candidates:
        total_score = (
            candidate["score"]
            +
            candidate_support(
                candidate,
                candidates
            )
        )

        scored.append({
            **candidate,
            "total_score": total_score
        })

    scored.sort(
        key=lambda x: x["total_score"],
        reverse=True
    )

    winner = scored[0]

    unique = unique_numbers(
        [
            item["value"]
            for item in scored
        ]
    )

    conflict = False

    for value in unique:
        difference = abs(
            value - winner["value"]
        ) / winner["value"]

        if difference >= 0.25:
            conflict = True
            break

    return (
        winner["value"],
        winner["source"],
        scored,
        conflict
    )


def extract_price(
    soup,
    json_objects,
    description,
    page_text
):
    candidates = []

    collect_json_prices(
        json_objects,
        candidates
    )

    collect_meta_prices(
        soup,
        candidates
    )

    collect_description_prices(
        description,
        candidates
    )

    collect_page_prices(
        page_text,
        candidates
    )

    return select_price(
        candidates
    )


# ============================================================
# AREA ENGINE
# ============================================================

def valid_area(value):
    return (
        value is not None
        and 20 <= value <= 100000
    )


def add_area_candidate(
    candidates,
    value,
    area_type,
    source,
    score
):
    number = numeric_value(value)

    if not valid_area(number):
        return

    candidates.append({
        "value": number,
        "area_type": area_type,
        "source": source,
        "score": score
    })


def collect_json_areas(
    json_objects,
    candidates
):
    built_keys = [
        "floorSize",
        "livingArea",
        "surface",
        "area",
        "size"
    ]

    plot_keys = [
        "lotSize",
        "landArea",
        "plotSize"
    ]

    for root in json_objects:
        for item in walk_json(root):

            for key in built_keys:
                if key not in item:
                    continue

                value = item.get(key)

                if isinstance(value, dict):
                    value = (
                        value.get("value")
                        or value.get("maxValue")
                        or value.get("minValue")
                    )

                add_area_candidate(
                    candidates,
                    value,
                    "built",
                    "json_ld_" + key,
                    95
                )

            for key in plot_keys:
                if key not in item:
                    continue

                value = item.get(key)

                if isinstance(value, dict):
                    value = (
                        value.get("value")
                        or value.get("maxValue")
                        or value.get("minValue")
                    )

                add_area_candidate(
                    candidates,
                    value,
                    "plot",
                    "json_ld_" + key,
                    95
                )


def collect_labelled_areas(
    text,
    candidates,
    source,
    score
):
    built_patterns = [
        r"(?:built area|built surface|"
        r"living area|habitable area|"
        r"surface habitable|"
        r"surface construite|"
        r"constructed area)"
        r"\s*[:\-]?\s*"
        r"(\d{2,6})"
        r"\s*(?:m²|m2|sqm)",

        r"(\d{2,6})"
        r"\s*(?:m²|m2|sqm)"
        r"\s*(?:built|living|"
        r"habitable|constructed)"
    ]

    plot_patterns = [
        r"(?:plot area|plot size|"
        r"land area|land size|"
        r"surface terrain|terrain)"
        r"\s*[:\-]?\s*"
        r"(\d{2,6})"
        r"\s*(?:m²|m2|sqm)",

        r"(\d{2,6})"
        r"\s*(?:m²|m2|sqm)"
        r"\s*(?:plot|land|terrain)"
    ]

    for pattern in built_patterns:
        matches = re.findall(
            pattern,
            text,
            re.IGNORECASE
        )

        for value in matches[:10]:
            add_area_candidate(
                candidates,
                value,
                "built",
                source,
                score
            )

    for pattern in plot_patterns:
        matches = re.findall(
            pattern,
            text,
            re.IGNORECASE
        )

        for value in matches[:10]:
            add_area_candidate(
                candidates,
                value,
                "plot",
                source,
                score
            )


def select_area(
    candidates,
    area_type
):
    relevant = [
        item
        for item in candidates
        if item["area_type"] == area_type
    ]

    if not relevant:
        return None, None, False

    grouped = {}

    for item in relevant:
        value = item["value"]

        if value not in grouped:
            grouped[value] = {
                "score": 0,
                "sources": []
            }

        grouped[value]["score"] += (
            item["score"]
        )

        grouped[value]["sources"].append(
            item["source"]
        )

    ranked = sorted(
        grouped.items(),
        key=lambda item: item[1]["score"],
        reverse=True
    )

    winner_value = ranked[0][0]
    winner_source = ranked[0][1][
        "sources"
    ][0]

    conflict = False

    for value, info in ranked[1:]:
        difference = abs(
            value - winner_value
        ) / winner_value

        if difference >= 0.15:
            conflict = True
            break

    return (
        winner_value,
        winner_source,
        conflict
    )


def extract_areas(
    json_objects,
    description,
    page_text
):
    candidates = []

    collect_json_areas(
        json_objects,
        candidates
    )

    collect_labelled_areas(
        description,
        candidates,
        "description",
        80
    )

    collect_labelled_areas(
        page_text,
        candidates,
        "page_text",
        50
    )

    built_area, built_source, built_conflict = (
        select_area(
            candidates,
            "built"
        )
    )

    plot_area, plot_source, plot_conflict = (
        select_area(
            candidates,
            "plot"
        )
    )

    # Fallback only if labelled data is missing.
    # We intentionally do NOT blindly assign every m² number.

    if built_area is None:
        generic = re.findall(
            r"(\d{2,6})\s*(?:m²|m2|sqm)",
            description,
            re.IGNORECASE
        )

        values = unique_numbers(
            [
                numeric_value(x)
                for x in generic
                if valid_area(
                    numeric_value(x)
                )
            ]
        )

        if len(values) == 1:
            built_area = values[0]
            built_source = (
                "description_unlabelled"
            )

    return {
        "built_area": built_area,
        "plot_area": plot_area,
        "built_source": built_source,
        "plot_source": plot_source,
        "built_conflict": built_conflict,
        "plot_conflict": plot_conflict,
        "candidates": candidates
    }


# ============================================================
# BEDROOMS
# ============================================================

def extract_bedrooms(text):
    patterns = [
        r"(\d+)\s*bedrooms?",
        r"(\d+)\s*beds?",
        r"(\d+)\s*chambres?",
        r"(\d+)\s*غرف",
        r"(\d+)\s*rooms?"
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            text,
            re.IGNORECASE
        )

        if match:
            value = safe_int(
                match.group(1)
            )

            if (
                value is not None
                and 1 <= value <= 30
            ):
                return value

    return None


# ============================================================
# PROPERTY TYPE
# ============================================================

def detect_property_type(text):
    text = normalise_text(text)

    types = [
        ("villa", "Villa"),
        ("riad", "Riad"),
        ("penthouse", "Penthouse"),
        ("duplex", "Duplex"),
        ("studio", "Studio"),
        ("apartment", "Apartment"),
        ("appartement", "Apartment"),
        ("flat", "Apartment"),
        ("house", "House"),
        ("maison", "House"),
        ("terrain", "Land"),
        ("land", "Land")
    ]

    for keyword, label in types:
        if keyword in text:
            return label

    return None


# ============================================================
# LOCATION
# ============================================================

def detect_city(text):
    text = normalise_text(text)

    cities = {
        "marrakech": "Marrakech",
        "casablanca": "Casablanca",
        "rabat": "Rabat",
        "tangier": "Tangier",
        "agadir": "Agadir",
        "fes": "Fes",
        "meknes": "Meknes",
        "tetouan": "Tetouan",
        "essaouira": "Essaouira",
        "kenitra": "Kenitra",
        "el jadida": "El Jadida",
        "oujda": "Oujda",
        "mohammedia": "Mohammedia"
    }

    for key, value in cities.items():
        if key in text:
            return value

    return None


MICRO_LOCATIONS = {
    "Marrakech": [
        "Route d'Amizmiz",
        "Route de l'Ourika",
        "Route de Casablanca",
        "Route de Fes",
        "Route de Ouarzazate",
        "Route de Tahanaout",
        "Hivernage",
        "Gueliz",
        "Agdal",
        "Palmeraie",
        "Medina",
        "Sidi Ghanem",
        "Targa",
        "Amerchich",
        "Chrifia",
        "Mhamid",
        "Daoudiate",
        "Majorelle",
        "Victor Hugo"
    ],

    "Casablanca": [
        "Ain Diab",
        "Anfa",
        "Maarif",
        "Racine",
        "Palmier",
        "Bourgogne",
        "Californie",
        "Oasis",
        "CIL",
        "Gauthier",
        "Sidi Maarouf"
    ],

    "Rabat": [
        "Agdal",
        "Hay Riad",
        "Souissi",
        "Hassan",
        "Ocean",
        "Aviation"
    ],

    "Tangier": [
        "Malabata",
        "Iberia",
        "Marshan",
        "City Center"
    ]
}


def detect_micro_location(
    text,
    city
):
    if not city:
        return None

    text = normalise_text(text)

    aliases = {
        "route d'amizmiz": [
            "route d'amizmiz",
            "route amizmiz",
            "route d'amezmiz",
            "amizmiz road"
        ],

        "route de l'ourika": [
            "route de l'ourika",
            "route ourika",
            "ourika road"
        ]
    }

    for location in MICRO_LOCATIONS.get(
        city,
        []
    ):
        normal = normalise_text(
            location
        )

        names = aliases.get(
            normal,
            [normal]
        )

        for name in names:
            if name in text:
                return location

    return None


# ============================================================
# SOURCE
# ============================================================

def detect_source(url):
    host = urlparse(
        url
    ).netloc.lower()

    host = host.replace(
        "www.",
        ""
    )

    if "mubawab" in host:
        return "Mubawab"

    if "sarout" in host:
        return "Sarout"

    if "avito" in host:
        return "Avito"

    return host or "Unknown"


# ============================================================
# MARKET BENCHMARKS
# ============================================================

def load_market_benchmarks():
    try:
        with open(
            DATA_FILE,
            "r",
            encoding="utf-8"
        ) as file:
            data = json.load(file)

        benchmarks = data.get(
            "benchmarks",
            []
        )

        if isinstance(
            benchmarks,
            list
        ):
            return benchmarks

    except Exception:
        pass

    return []


def benchmark_match_score(
    benchmark,
    city,
    micro_location,
    property_type
):
    score = 0

    b_city = normalise_text(
        benchmark.get("city")
    )

    b_location = normalise_text(
        benchmark.get(
            "micro_location"
        )
    )

    b_type = normalise_text(
        benchmark.get(
            "property_type"
        )
    )

    if city:
        if b_city != normalise_text(city):
            return -1

        score += 30

    if property_type:
        if b_type == normalise_text(
            property_type
        ):
            score += 30

        elif b_type:
            return -1

    if micro_location:
        if b_location == normalise_text(
            micro_location
        ):
            score += 40

        elif b_location:
            score += 5

    return score


def find_best_benchmarks(
    city,
    micro_location,
    property_type
):
    matches = []

    for benchmark in (
        load_market_benchmarks()
    ):
        value = safe_float(
            benchmark.get(
                "benchmark_mad_m2"
            )
        )

        if not value or value <= 0:
            continue

        score = benchmark_match_score(
            benchmark,
            city,
            micro_location,
            property_type
        )

        if score < 0:
            continue

        matches.append({
            "benchmark": benchmark,
            "match_score": score
        })

    if not matches:
        return []

    matches.sort(
        key=lambda x: x[
            "match_score"
        ],
        reverse=True
    )

    best = matches[0][
        "match_score"
    ]

    return [
        item
        for item in matches
        if item["match_score"] == best
    ]


def confidence_weight(value):
    weights = {
        "high": 1.0,
        "medium": 0.75,
        "low": 0.5
    }

    return weights.get(
        normalise_text(value),
        0.6
    )


def calculate_weighted_benchmark(
    matches
):
    total = 0
    total_weight = 0
    sample_total = 0
    used = []

    for match in matches:
        benchmark = match[
            "benchmark"
        ]

        value = safe_float(
            benchmark.get(
                "benchmark_mad_m2"
            )
        )

        if not value:
            continue

        sample = safe_int(
            benchmark.get(
                "sample_size"
            )
        ) or 1

        weight = (
            confidence_weight(
                benchmark.get(
                    "confidence"
                )
            )
            *
            min(sample, 100)
        )

        total += value * weight
        total_weight += weight
        sample_total += sample

        used.append({
            "city": benchmark.get(
                "city"
            ),
            "micro_location":
                benchmark.get(
                    "micro_location"
                ),
            "property_type":
                benchmark.get(
                    "property_type"
                ),
            "benchmark_mad_m2":
                value,
            "sample_size":
                sample,
            "source":
                benchmark.get(
                    "source"
                ),
            "source_date":
                benchmark.get(
                    "source_date"
                ),
            "confidence":
                benchmark.get(
                    "confidence"
                ),
            "match_score":
                match[
                    "match_score"
                ]
        })

    if not total_weight:
        return None, 0, []

    return (
        round(
            total /
            total_weight
        ),
        sample_total,
        used
    )


# ============================================================
# MARKET ADJUSTMENTS
# ============================================================

def distance_adjustment(
    distance_km
):
    distance = safe_float(
        distance_km
    )

    if distance is None:
        return 1.0, None

    if distance <= 3:
        factor = 1.08

    elif distance <= 6:
        factor = 1.04

    elif distance <= 10:
        factor = 1.00

    elif distance <= 15:
        factor = 0.96

    elif distance <= 20:
        factor = 0.92

    elif distance <= 30:
        factor = 0.87

    else:
        factor = 0.82

    return factor, distance


def size_adjustment(
    built_area
):
    area = safe_float(
        built_area
    )

    if area is None:
        return 1.0

    if area <= 100:
        return 1.05

    if area <= 200:
        return 1.02

    if area <= 350:
        return 1.00

    if area <= 500:
        return 0.98

    if area <= 750:
        return 0.95

    return 0.92


def determine_market_position(
    difference
):
    if difference is None:
        return None

    if difference <= -20:
        return (
            "Potentially well below "
            "local benchmark"
        )

    if difference <= -8:
        return (
            "Potentially below "
            "local benchmark"
        )

    if difference < 8:
        return (
            "Broadly aligned with "
            "local benchmark"
        )

    if difference < 20:
        return (
            "Potentially above "
            "local benchmark"
        )

    return (
        "Potentially well above "
        "local benchmark"
    )


# ============================================================
# MARKET ANALYSIS
# ============================================================

def analyse_market(
    price,
    built_area,
    city,
    micro_location,
    property_type,
    distance_km=None
):
    result = {
        "available": False,
        "reason": None,
        "listing_mad_m2": None,
        "raw_benchmark_mad_m2": None,
        "adjusted_benchmark_mad_m2": None,
        "benchmark_mad_m2": None,
        "difference_percent": None,
        "market_position": None,
        "sample_size": 0,
        "distance_km": None,
        "distance_factor": 1.0,
        "size_factor": 1.0,
        "comparables": []
    }

    if not price:
        result["reason"] = (
            "Price required"
        )
        return result

    if not built_area:
        result["reason"] = (
            "Built area required"
        )
        return result

    listing_m2 = round(
        price /
        built_area
    )

    result[
        "listing_mad_m2"
    ] = listing_m2

    matches = find_best_benchmarks(
        city,
        micro_location,
        property_type
    )

    if not matches:
        result["reason"] = (
            "No suitable local "
            "benchmark available"
        )
        return result

    (
        raw_benchmark,
        sample_size,
        comparables
    ) = calculate_weighted_benchmark(
        matches
    )

    if not raw_benchmark:
        result["reason"] = (
            "Benchmark data insufficient"
        )
        return result

    distance_factor, distance = (
        distance_adjustment(
            distance_km
        )
    )

    size_factor = size_adjustment(
        built_area
    )

    adjusted = round(
        raw_benchmark
        *
        distance_factor
        *
        size_factor
    )

    difference = round(
        (
            (
                listing_m2
                -
                adjusted
            )
            /
            adjusted
        )
        *
        100,
        1
    )

    result.update({
        "available": True,
        "listing_mad_m2":
            listing_m2,
        "raw_benchmark_mad_m2":
            raw_benchmark,
        "adjusted_benchmark_mad_m2":
            adjusted,
        "benchmark_mad_m2":
            adjusted,
        "difference_percent":
            difference,
        "market_position":
            determine_market_position(
                difference
            ),
        "sample_size":
            sample_size,
        "distance_km":
            distance,
        "distance_factor":
            distance_factor,
        "size_factor":
            size_factor,
        "comparables":
            comparables
    })

    return result


# ============================================================
# CONFIDENCE
# ============================================================

def calculate_confidence(
    price,
    built_area,
    plot_area,
    bedrooms,
    property_type,
    city,
    micro_location,
    price_conflict,
    built_conflict,
    plot_conflict
):
    score = 0

    if price:
        score += 25

    if built_area:
        score += 20

    if property_type:
        score += 15

    if city:
        score += 15

    if bedrooms:
        score += 10

    if micro_location:
        score += 10

    if plot_area:
        score += 5

    if price_conflict:
        score -= 20

    if built_conflict:
        score -= 12

    if plot_conflict:
        score -= 8

    return max(
        0,
        min(
            100,
            score
        )
    )


def calculate_radar_score(
    difference,
    sample_size,
    micro_location,
    built_area
):
    score = 50

    if difference is not None:
        if difference <= -20:
            score += 30

        elif difference <= -10:
            score += 22

        elif difference < 0:
            score += 12

        elif difference <= 10:
            score += 5

        elif difference <= 20:
            score -= 5

        else:
            score -= 15

    if sample_size >= 50:
        score += 10

    elif sample_size >= 20:
        score += 7

    elif sample_size >= 10:
        score += 4

    if micro_location:
        score += 5

    if built_area:
        score += 5

    return max(
        0,
        min(
            100,
            round(score)
        )
    )


# ============================================================
# SCANNER
# ============================================================

@app.route(
    "/api/scan",
    methods=["POST"]
)
def scan_property():

    payload = (
        request.get_json(
            silent=True
        )
        or {}
    )

    url = clean_text(
        payload.get("url")
    )

    supplied_distance = (
        safe_float(
            payload.get(
                "distance_from_centre_km"
            )
        )
    )

    if not url:
        return jsonify({
            "success": False,
            "error":
                "No property URL supplied."
        }), 400

    if not url.startswith(
        ("http://", "https://")
    ):
        return jsonify({
            "success": False,
            "error":
                "Invalid property URL."
        }), 400

    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        title = extract_title(
            soup
        )

        description = (
            extract_description(
                soup
            )
        )

        page_text = clean_text(
            soup.get_text(
                " ",
                strip=True
            )
        )

        json_objects = get_json_ld(
            soup
        )

        # ----------------------------------------
        # PRICE
        # ----------------------------------------

        (
            price,
            price_source,
            price_candidates,
            price_conflict
        ) = extract_price(
            soup,
            json_objects,
            description,
            page_text
        )

        # ----------------------------------------
        # AREAS
        # ----------------------------------------

        area_result = extract_areas(
            json_objects,
            description,
            page_text
        )

        built_area = area_result[
            "built_area"
        ]

        plot_area = area_result[
            "plot_area"
        ]

        # ----------------------------------------
        # OTHER DATA
        # ----------------------------------------

        priority_text = clean_text(
            " ".join([
                title,
                description
            ])
        )

        bedrooms = extract_bedrooms(
            priority_text
        )

        if bedrooms is None:
            bedrooms = extract_bedrooms(
                page_text
            )

        property_type = (
            detect_property_type(
                priority_text
            )
        )

        if not property_type:
            property_type = (
                detect_property_type(
                    page_text
                )
            )

        city = detect_city(
            priority_text
        )

        if not city:
            city = detect_city(
                page_text
            )

        micro_location = (
            detect_micro_location(
                priority_text,
                city
            )
        )

        if not micro_location:
            micro_location = (
                detect_micro_location(
                    page_text,
                    city
                )
            )

        source_name = (
            detect_source(url)
        )

        # ----------------------------------------
        # PRICE / M2
        # ----------------------------------------

        price_per_m2 = None

        if price and built_area:
            price_per_m2 = round(
                price /
                built_area
            )

        # ----------------------------------------
        # CONFIDENCE
        # ----------------------------------------

        confidence = (
            calculate_confidence(
                price,
                built_area,
                plot_area,
                bedrooms,
                property_type,
                city,
                micro_location,
                price_conflict,
                area_result[
                    "built_conflict"
                ],
                area_result[
                    "plot_conflict"
                ]
            )
        )

        # ----------------------------------------
        # MARKET
        # ----------------------------------------

        market = analyse_market(
            price=price,
            built_area=built_area,
            city=city,
            micro_location=
                micro_location,
            property_type=
                property_type,
            distance_km=
                supplied_distance
        )

        radar_score = None

        if market["available"]:
            radar_status = (
                market[
                    "market_position"
                ]
            )

            radar_score = (
                calculate_radar_score(
                    market[
                        "difference_percent"
                    ],
                    market[
                        "sample_size"
                    ],
                    micro_location,
                    built_area
                )
            )

        elif not price:
            radar_status = (
                "PRICE REQUIRED"
            )

        elif not built_area:
            radar_status = (
                "PRICE VERIFIED - "
                "AREA REQUIRED"
            )

        else:
            radar_status = (
                "MARKET BENCHMARK "
                "UNAVAILABLE"
            )

        # ----------------------------------------
        # VALIDATION FLAGS
        # ----------------------------------------

        flags = []

        if price_conflict:
            flags.append(
                "Conflicting price values "
                "detected"
            )

        if area_result[
            "built_conflict"
        ]:
            flags.append(
                "Conflicting built-area "
                "values detected"
            )

        if area_result[
            "plot_conflict"
        ]:
            flags.append(
                "Conflicting plot-area "
                "values detected"
            )

        # ----------------------------------------
        # RESPONSE
        # ----------------------------------------

        return jsonify({
            "success": True,

            "source": {
                "url": url,
                "website": source_name,
                "title": title,
                "description":
                    description,
                "price_source":
                    price_source
            },

            "property": {
                "city": city,
                "micro_location":
                    micro_location,
                "property_type":
                    property_type,
                "price_mad":
                    price,
                "built_area_m2":
                    built_area,
                "plot_area_m2":
                    plot_area,
                "bedrooms":
                    bedrooms,
                "price_per_built_m2_mad":
                    price_per_m2
            },

            "validation": {
                "data_confidence":
                    confidence,
                "price_conflict":
                    price_conflict,
                "built_area_conflict":
                    area_result[
                        "built_conflict"
                    ],
                "plot_area_conflict":
                    area_result[
                        "plot_conflict"
                    ],
                "flags":
                    flags
            },

            "market_analysis":
                market,

            "radar": {
                "status":
                    radar_status,
                "radar_score":
                    radar_score,
                "data_confidence":
                    confidence
            },

            "debug": {
                "price_candidates":
                    price_candidates[:10],
                "area_candidates":
                    area_result[
                        "candidates"
                    ][:15]
            }
        })

    except requests.RequestException as error:
        return jsonify({
            "success": False,
            "error":
                "Property listing could "
                "not be retrieved.",
            "details":
                str(error)
        }), 502

    except Exception as error:
        return jsonify({
            "success": False,
            "error":
                "Property scan failed.",
            "details":
                str(error)
        }), 500


# ============================================================
# LOCAL DEVELOPMENT
# ============================================================

if __name__ == "__main__":
    app.run(debug=True)
