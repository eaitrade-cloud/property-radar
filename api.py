import json
import os
import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify
from flask_cors import CORS


app = Flask(__name__)
CORS(app)


# ============================================================
# CONFIGURATION
# ============================================================

REQUEST_TIMEOUT = 15

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/138.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en;q=0.9,fr;q=0.8",
}

DATA_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "data",
    "market_benchmarks.json"
)


# ============================================================
# HOME
# ============================================================

@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "ok",
        "service": "Property Radar API",
        "version": "2.0"
    })


# ============================================================
# BASIC HELPERS
# ============================================================

def clean_text(text):
    return re.sub(r"\s+", " ", text or "").strip()


def normalise_text(text):
    text = clean_text(text).lower()

    replacements = {
        "’": "'",
        "`": "'",
        "â€™": "'",
        "amezmiz": "amizmiz",
        "tanger": "tangier",
        "fez": "fes"
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    return text


def safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def extract_number(value):
    if not value:
        return None

    value = str(value).replace("\xa0", " ")

    matches = re.findall(
        r"\d[\d\s.,]*",
        value
    )

    if not matches:
        return None

    number = matches[0]

    number = re.sub(
        r"[^\d]",
        "",
        number
    )

    if not number:
        return None

    try:
        return int(number)
    except ValueError:
        return None


# ============================================================
# JSON-LD
# ============================================================

def get_json_ld(soup):
    objects = []

    scripts = soup.find_all(
        "script",
        attrs={"type": "application/ld+json"}
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
                objects.extend(data)

            elif isinstance(data, dict):

                if "@graph" in data:
                    graph = data.get("@graph")

                    if isinstance(graph, list):
                        objects.extend(graph)

                objects.append(data)

        except Exception:
            continue

    return objects


def walk_json(data):

    if isinstance(data, dict):

        yield data

        for value in data.values():
            yield from walk_json(value)

    elif isinstance(data, list):

        for item in data:
            yield from walk_json(item)


# ============================================================
# PRICE EXTRACTION
# ============================================================

def valid_property_price(price):

    if price is None:
        return False

    return 100000 <= price <= 200000000


def extract_price_from_json_ld(json_objects):

    candidates = []

    for root in json_objects:

        for item in walk_json(root):

            for key in [
                "price",
                "lowPrice",
                "highPrice"
            ]:

                if key not in item:
                    continue

                value = extract_number(
                    str(item.get(key))
                )

                if valid_property_price(value):
                    candidates.append(value)

    if not candidates:
        return None

    return min(candidates)


def extract_price_from_meta(soup):

    selectors = [
        ("meta", {"property": "product:price:amount"}),
        ("meta", {"property": "og:price:amount"}),
        ("meta", {"itemprop": "price"}),
        ("meta", {"name": "price"})
    ]

    candidates = []

    for tag_name, attrs in selectors:

        tags = soup.find_all(
            tag_name,
            attrs=attrs
        )

        for tag in tags:

            value = extract_number(
                tag.get("content", "")
            )

            if valid_property_price(value):
                candidates.append(value)

    if not candidates:
        return None

    return min(candidates)


def extract_price_from_text(text):

    patterns = [

        r"(?:price|prix|asking price)"
        r"\s*[:\-]?\s*"
        r"(\d[\d\s.,]{3,})"
        r"\s*(?:MAD|DH|DHS)",

        r"(\d[\d\s.,]{3,})"
        r"\s*(?:MAD|DH|DHS)",

        r"(?:MAD|DH|DHS)"
        r"\s*(\d[\d\s.,]{3,})",

        r"(\d[\d\s.,]{3,})"
        r"\s*درهم"
    ]

    candidates = []

    for pattern in patterns:

        matches = re.findall(
            pattern,
            text,
            re.IGNORECASE
        )

        for match in matches:

            value = extract_number(match)

            if valid_property_price(value):
                candidates.append(value)

    if not candidates:
        return None

    return min(candidates)


def extract_price(
    soup,
    json_objects,
    analysis_text
):

    price = extract_price_from_json_ld(
        json_objects
    )

    if price:
        return price, "structured_data"

    price = extract_price_from_meta(soup)

    if price:
        return price, "metadata"

    price = extract_price_from_text(
        analysis_text
    )

    if price:
        return price, "page_text"

    return None, None


# ============================================================
# AREA EXTRACTION
# ============================================================

def valid_area(area):

    if area is None:
        return False

    return 20 <= area <= 100000


def find_area(patterns, text):

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE
        )

        if not match:
            continue

        area = safe_int(
            match.group(1)
        )

        if valid_area(area):
            return area

    return None


def extract_built_area(text):

    patterns = [

        r"(?:built area|built surface|living area|"
        r"habitable area|surface habitable|"
        r"surface construite|constructed area)"
        r"\s*[:\-]?\s*(\d{2,5})\s*(?:m²|m2|sqm)",

        r"(\d{2,5})\s*(?:m²|m2|sqm)"
        r"\s*(?:built|living|habitable|constructed)"
    ]

    return find_area(
        patterns,
        text
    )


def extract_plot_area(text):

    patterns = [

        r"(?:plot area|plot size|land area|"
        r"land size|terrain|surface terrain)"
        r"\s*[:\-]?\s*(\d{2,6})\s*(?:m²|m2|sqm)",

        r"(\d{2,6})\s*(?:m²|m2|sqm)"
        r"\s*(?:plot|land|terrain)"
    ]

    return find_area(
        patterns,
        text
    )


def extract_all_areas(text):

    matches = re.findall(
        r"(\d{2,6})\s*(?:m²|m2|sqm)",
        text,
        re.IGNORECASE
    )

    areas = []

    for value in matches:

        area = safe_int(value)

        if valid_area(area):
            areas.append(area)

    return list(dict.fromkeys(areas))


def determine_areas(text):

    built_area = extract_built_area(text)
    plot_area = extract_plot_area(text)

    all_areas = extract_all_areas(text)

    if (
        built_area is None
        and plot_area is None
        and len(all_areas) == 1
    ):
        built_area = all_areas[0]

    if (
        built_area is None
        and plot_area is None
        and len(all_areas) >= 2
    ):

        sorted_areas = sorted(
            all_areas
        )

        built_area = sorted_areas[0]
        plot_area = sorted_areas[-1]

    return built_area, plot_area


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

        if not match:
            continue

        bedrooms = safe_int(
            match.group(1)
        )

        if (
            bedrooms is not None
            and 1 <= bedrooms <= 30
        ):
            return bedrooms

    return None


# ============================================================
# PROPERTY TYPE
# ============================================================

def detect_property_type(text):

    property_types = [

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

    lower_text = normalise_text(text)

    for keyword, label in property_types:

        if keyword in lower_text:
            return label

    return None


# ============================================================
# CITY
# ============================================================

def detect_city(text):

    cities = {
        "marrakech": "Marrakech",
        "marrakesh": "Marrakech",
        "casablanca": "Casablanca",
        "rabat": "Rabat",
        "tangier": "Tangier",
        "tanger": "Tangier",
        "agadir": "Agadir",
        "fes": "Fes",
        "fez": "Fes",
        "meknes": "Meknes",
        "tetouan": "Tetouan",
        "essaouira": "Essaouira",
        "kenitra": "Kenitra",
        "el jadida": "El Jadida",
        "oujda": "Oujda",
        "mohammedia": "Mohammedia"
    }

    lower_text = normalise_text(text)

    for keyword, city in cities.items():

        if keyword in lower_text:
            return city

    return None


# ============================================================
# MICRO LOCATION
# ============================================================

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


def detect_micro_location(text, city):

    if not city:
        return None

    locations = MICRO_LOCATIONS.get(
        city,
        []
    )

    lower_text = normalise_text(text)

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

    for location in locations:

        normal_location = normalise_text(
            location
        )

        possible_names = aliases.get(
            normal_location,
            [normal_location]
        )

        for name in possible_names:

            if name in lower_text:
                return location

    return None


# ============================================================
# SOURCE DETECTION
# ============================================================

def detect_source(url):

    hostname = urlparse(
        url
    ).netloc.lower()

    hostname = hostname.replace(
        "www.",
        ""
    )

    if "mubawab" in hostname:
        return "Mubawab"

    if "sarout" in hostname:
        return "Sarout"

    if "avito" in hostname:
        return "Avito"

    return hostname or "Unknown"


# ============================================================
# MARKET BENCHMARK DATA
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

        if not isinstance(
            benchmarks,
            list
        ):
            return []

        return benchmarks

    except Exception:
        return []


# ============================================================
# BENCHMARK MATCHING
# ============================================================

def benchmark_match_score(
    benchmark,
    city,
    micro_location,
    property_type
):

    score = 0

    benchmark_city = normalise_text(
        benchmark.get("city", "")
    )

    benchmark_location = normalise_text(
        benchmark.get(
            "micro_location",
            ""
        )
    )

    benchmark_type = normalise_text(
        benchmark.get(
            "property_type",
            ""
        )
    )

    if city:

        if (
            benchmark_city
            != normalise_text(city)
        ):
            return -1

        score += 30

    if property_type:

        if (
            benchmark_type
            == normalise_text(
                property_type
            )
        ):
            score += 30

        elif benchmark_type:
            return -1

    if micro_location:

        if (
            benchmark_location
            == normalise_text(
                micro_location
            )
        ):
            score += 40

        elif benchmark_location:
            score += 5

    return score


def find_best_benchmarks(
    city,
    micro_location,
    property_type
):

    benchmarks = load_market_benchmarks()

    matches = []

    for benchmark in benchmarks:

        benchmark_value = safe_float(
            benchmark.get(
                "benchmark_mad_m2"
            )
        )

        if (
            benchmark_value is None
            or benchmark_value <= 0
        ):
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

    matches.sort(
        key=lambda item:
            item["match_score"],
        reverse=True
    )

    if not matches:
        return []

    best_score = matches[0][
        "match_score"
    ]

    return [
        item
        for item in matches
        if item["match_score"]
        == best_score
    ]


# ============================================================
# WEIGHTED BENCHMARK
# ============================================================

def confidence_weight(confidence):

    confidence = normalise_text(
        confidence
    )

    weights = {
        "high": 1.0,
        "medium": 0.75,
        "low": 0.5
    }

    return weights.get(
        confidence,
        0.6
    )


def calculate_weighted_benchmark(
    matches
):

    if not matches:
        return None, 0, []

    weighted_total = 0
    total_weight = 0
    total_sample = 0

    used = []

    for item in matches:

        benchmark = item[
            "benchmark"
        ]

        value = safe_float(
            benchmark.get(
                "benchmark_mad_m2"
            )
        )

        if not value:
            continue

        sample_size = safe_int(
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
            min(
                max(
                    sample_size,
                    1
                ),
                100
            )
        )

        weighted_total += (
            value * weight
        )

        total_weight += weight
        total_sample += sample_size

        used.append({
            "city":
                benchmark.get("city"),

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
                sample_size,

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
                item.get(
                    "match_score"
                )
        })

    if total_weight == 0:
        return None, 0, []

    benchmark_value = round(
        weighted_total /
        total_weight
    )

    return (
        benchmark_value,
        total_sample,
        used
    )


# ============================================================
# DISTANCE ADJUSTMENT
# ============================================================

def distance_adjustment(
    distance_km
):

    if distance_km is None:
        return 1.0, None

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


# ============================================================
# SIZE ADJUSTMENT
# ============================================================

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


# ============================================================
# MARKET POSITION
# ============================================================

def determine_market_position(
    difference_percent
):

    if difference_percent is None:
        return None

    if difference_percent <= -20:
        return (
            "Potentially well below "
            "local benchmark"
        )

    if difference_percent <= -8:
        return (
            "Potentially below "
            "local benchmark"
        )

    if difference_percent < 8:
        return (
            "Broadly aligned with "
            "local benchmark"
        )

    if difference_percent < 20:
        return (
            "Potentially above "
            "local benchmark"
        )

    return (
        "Potentially well above "
        "local benchmark"
    )


# ============================================================
# RADAR SCORE
# ============================================================

def calculate_radar_score(
    difference_percent,
    sample_size,
    micro_location,
    built_area
):

    score = 50

    if difference_percent is not None:

        if difference_percent <= -20:
            score += 30

        elif difference_percent <= -10:
            score += 22

        elif difference_percent < 0:
            score += 12

        elif difference_percent <= 10:
            score += 5

        elif difference_percent <= 20:
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
# DATA CONFIDENCE
# ============================================================

def calculate_data_confidence(
    price,
    built_area,
    bedrooms,
    property_type,
    city,
    micro_location,
    plot_area
):

    points = 0
    maximum = 100

    if price:
        points += 25

    if built_area:
        points += 20

    if property_type:
        points += 15

    if city:
        points += 15

    if bedrooms:
        points += 10

    if micro_location:
        points += 10

    if plot_area:
        points += 5

    return round(
        points /
        maximum *
        100
    )


# ============================================================
# MARKET ANALYSIS ENGINE
# ============================================================

def analyse_market(
    price,
    built_area,
    plot_area,
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

    distance_factor, resolved_distance = (
        distance_adjustment(
            distance_km
        )
    )

    area_factor = size_adjustment(
        built_area
    )

    adjusted_benchmark = round(
        raw_benchmark
        *
        distance_factor
        *
        area_factor
    )

    difference = round(
        (
            (
                listing_m2
                -
                adjusted_benchmark
            )
            /
            adjusted_benchmark
        )
        *
        100,
        1
    )

    market_position = (
        determine_market_position(
            difference
        )
    )

    result.update({
        "available": True,

        "listing_mad_m2":
            listing_m2,

        "raw_benchmark_mad_m2":
            raw_benchmark,

        "adjusted_benchmark_mad_m2":
            adjusted_benchmark,

        "benchmark_mad_m2":
            adjusted_benchmark,

        "difference_percent":
            difference,

        "market_position":
            market_position,

        "sample_size":
            sample_size,

        "distance_km":
            resolved_distance,

        "distance_factor":
            distance_factor,

        "size_factor":
            area_factor,

        "comparables":
            comparables
    })

    return result


# ============================================================
# SCANNER
# ============================================================

@app.route(
    "/api/scan",
    methods=["POST"]
)
def scan_property():

    data = (
        request.get_json(
            silent=True
        )
        or {}
    )

    url = clean_text(
        data.get(
            "url",
            ""
        )
    )

    supplied_distance = safe_float(
        data.get(
            "distance_from_centre_km"
        )
    )

    if not url:

        return jsonify({
            "success": False,
            "error":
                "No property URL supplied."
        }), 400

    if not url.startswith(
        (
            "http://",
            "https://"
        )
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

        # ----------------------------------------
        # TITLE
        # ----------------------------------------

        title = ""

        og_title = soup.find(
            "meta",
            attrs={
                "property": "og:title"
            }
        )

        if og_title:

            title = clean_text(
                og_title.get(
                    "content",
                    ""
                )
            )

        if (
            not title
            and soup.title
        ):

            title = clean_text(
                soup.title.get_text(
                    " ",
                    strip=True
                )
            )

        # ----------------------------------------
        # DESCRIPTION
        # ----------------------------------------

        description = ""

        meta_description = soup.find(
            "meta",
            attrs={
                "name": "description"
            }
        )

        if meta_description:

            description = clean_text(
                meta_description.get(
                    "content",
                    ""
                )
            )

        if not description:

            og_description = soup.find(
                "meta",
                attrs={
                    "property":
                        "og:description"
                }
            )

            if og_description:

                description = clean_text(
                    og_description.get(
                        "content",
                        ""
                    )
                )

        # ----------------------------------------
        # PAGE TEXT
        # ----------------------------------------

        page_text = clean_text(
            soup.get_text(
                " ",
                strip=True
            )
        )

        analysis_text = clean_text(
            " ".join([
                title,
                description,
                page_text[:30000]
            ])
        )

        # ----------------------------------------
        # STRUCTURED DATA
        # ----------------------------------------

        json_objects = get_json_ld(
            soup
        )

        # ----------------------------------------
        # PROPERTY DATA
        # ----------------------------------------

        price, price_source = (
            extract_price(
                soup,
                json_objects,
                analysis_text
            )
        )

        built_area, plot_area = (
            determine_areas(
                analysis_text
            )
        )

        bedrooms = extract_bedrooms(
            analysis_text
        )

        property_type = (
            detect_property_type(
                analysis_text
            )
        )

        city = detect_city(
            analysis_text
        )

        micro_location = (
            detect_micro_location(
                analysis_text,
                city
            )
        )

        source_name = detect_source(
            url
        )

        # ----------------------------------------
        # PRICE PER M2
        # ----------------------------------------

        price_per_built_m2 = None

        if (
            price
            and built_area
        ):

            price_per_built_m2 = round(
                price /
                built_area
            )

        # ----------------------------------------
        # CONFIDENCE
        # ----------------------------------------

        data_confidence = (
            calculate_data_confidence(
                price,
                built_area,
                bedrooms,
                property_type,
                city,
                micro_location,
                plot_area
            )
        )

        # ----------------------------------------
        # MARKET ENGINE
        # ----------------------------------------

        market_analysis = (
            analyse_market(
                price=price,
                built_area=built_area,
                plot_area=plot_area,
                city=city,
                micro_location=
                    micro_location,
                property_type=
                    property_type,
                distance_km=
                    supplied_distance
            )
        )

        # ----------------------------------------
        # RADAR STATUS
        # ----------------------------------------

        radar_score = None

        if market_analysis[
            "available"
        ]:

            radar_status = (
                market_analysis[
                    "market_position"
                ]
            )

            radar_score = (
                calculate_radar_score(
                    market_analysis[
                        "difference_percent"
                    ],

                    market_analysis[
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
        # RESPONSE
        # ----------------------------------------

        return jsonify({

            "success": True,

            "source": {

                "url": url,

                "website":
                    source_name,

                "title":
                    title,

                "description":
                    description,

                "price_source":
                    price_source
            },

            "property": {

                "city":
                    city,

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
                    price_per_built_m2
            },

            "market_analysis":
                market_analysis,

            "radar": {

                "status":
                    radar_status,

                "radar_score":
                    radar_score,

                "data_confidence":
                    data_confidence
            },

            "content":
                page_text[:12000]
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

    app.run(
        debug=True
    )
