import os
import re
import json
import requests

from urllib.parse import urlparse
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS


app = Flask(__name__)
CORS(app)


# =========================================================
# BASIC ROUTES
# =========================================================

@app.route("/", methods=["GET"])
def home():
    if os.path.exists("index.html"):
        return send_from_directory(".", "index.html")

    return jsonify({
        "status": "ok",
        "service": "Property Radar API"
    })


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "Property Radar Universal Scanner",
        "version": "2.1"
    })


# =========================================================
# GENERAL HELPERS
# =========================================================

def clean_text(value):
    if value is None:
        return ""

    return re.sub(r"\s+", " ", str(value)).strip()


def normalise_text(value):
    value = clean_text(value)

    replacements = {
        "\u00a0": " ",
        "㎡": "m2",
        "m²": "m2",
        "M²": "m2",
        "m^2": "m2"
    }

    for old, new in replacements.items():
        value = value.replace(old, new)

    return value


def safe_int(value):
    if value is None:
        return None

    text = str(value).strip()

    match = re.search(r"\d[\d\s,.]*", text)

    if not match:
        return None

    digits = re.sub(r"[^\d]", "", match.group())

    if not digits:
        return None

    try:
        return int(digits)
    except ValueError:
        return None


def valid_price(value):
    return (
        isinstance(value, int)
        and 100000 <= value <= 1000000000
    )


def valid_area(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None

    if 10 <= value <= 200000:
        return value

    return None


def unique_values(values):
    result = []

    for value in values:
        if value not in result:
            result.append(value)

    return result


def get_domain(url):
    try:
        domain = urlparse(url).netloc.lower()

        if domain.startswith("www."):
            domain = domain[4:]

        return domain

    except Exception:
        return ""


# =========================================================
# META DATA
# =========================================================

def get_meta(soup, name=None, prop=None):
    tag = None

    if name:
        tag = soup.find(
            "meta",
            attrs={"name": name}
        )

    if not tag and prop:
        tag = soup.find(
            "meta",
            attrs={"property": prop}
        )

    if not tag:
        return ""

    return clean_text(tag.get("content", ""))


# =========================================================
# JSON-LD
# =========================================================

def get_json_ld(soup):
    results = []

    scripts = soup.find_all(
        "script",
        attrs={"type": "application/ld+json"}
    )

    for script in scripts:
        raw = script.string or script.get_text()

        if not raw:
            continue

        try:
            parsed = json.loads(raw)
            results.append(parsed)

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


# =========================================================
# PRICE EXTRACTION
# =========================================================

def extract_structured_prices(json_data):
    candidates = []

    price_keys = {
        "price",
        "lowprice",
        "highprice",
        "amount",
        "priceamount"
    }

    for item in walk_json(json_data):

        for key, raw_value in item.items():

            if str(key).lower() not in price_keys:
                continue

            if isinstance(raw_value, (dict, list)):
                continue

            value = safe_int(raw_value)

            if valid_price(value):
                candidates.append({
                    "value": value,
                    "source": "structured_data",
                    "field": str(key)
                })

    return candidates


def extract_meta_prices(soup):
    candidates = []

    selectors = [
        ("property", "product:price:amount"),
        ("property", "og:price:amount"),
        ("name", "price"),
        ("itemprop", "price")
    ]

    for attribute, field in selectors:

        tag = soup.find(
            "meta",
            attrs={attribute: field}
        )

        if not tag:
            continue

        value = safe_int(tag.get("content", ""))

        if valid_price(value):
            candidates.append({
                "value": value,
                "source": "meta",
                "field": field
            })

    return candidates


def extract_visible_prices(text):
    candidates = []

    patterns = [
        r"(\d{1,3}(?:[\s,.]\d{3})+)\s*(?:MAD|DHS?|DH)\b",
        r"\b(?:MAD|DHS?|DH)\s*(\d{1,3}(?:[\s,.]\d{3})+)",
        r"(\d{5,10})\s*(?:MAD|DHS?|DH)\b",
        r"(\d{1,3}(?:[\s,.]\d{3})+)\s*درهم",
        r"(\d{5,10})\s*درهم"
    ]

    for pattern in patterns:

        for match in re.finditer(
            pattern,
            text,
            re.IGNORECASE
        ):
            value = safe_int(match.group(1))

            if valid_price(value):
                candidates.append({
                    "value": value,
                    "source": "visible_text",
                    "field": "currency_price"
                })

    return candidates


def choose_price(structured, metadata, visible):
    all_candidates = metadata + structured + visible

    if not all_candidates:
        return {
            "value": None,
            "confidence": "none",
            "source": None,
            "candidates": []
        }

    counts = {}

    for candidate in all_candidates:
        value = candidate["value"]
        counts[value] = counts.get(value, 0) + 1

    repeated = [
        value
        for value, count in counts.items()
        if count >= 2
    ]

    if repeated:
        repeated.sort(
            key=lambda value: (
                counts[value],
                -value
            ),
            reverse=True
        )

        value = repeated[0]

        return {
            "value": value,
            "confidence": "high",
            "source": "multiple_sources",
            "candidates": all_candidates
        }

    if metadata:
        return {
            "value": metadata[0]["value"],
            "confidence": "medium",
            "source": "meta",
            "candidates": all_candidates
        }

    if structured:
        return {
            "value": structured[0]["value"],
            "confidence": "medium",
            "source": "structured_data",
            "candidates": all_candidates
        }

    return {
        "value": visible[0]["value"],
        "confidence": "low",
        "source": "visible_text",
        "candidates": all_candidates
    }


# =========================================================
# AREA EXTRACTION
# =========================================================

def get_area_mentions(text):
    text = normalise_text(text)

    pattern = re.compile(
        r"\b(\d{2,6})\s*m2\b",
        re.IGNORECASE
    )

    mentions = []

    for match in pattern.finditer(text):

        value = valid_area(match.group(1))

        if not value:
            continue

        start = max(0, match.start() - 140)
        end = min(len(text), match.end() + 140)

        context = clean_text(text[start:end])

        mentions.append({
            "value": value,
            "context": context
        })

    return mentions


def classify_area_context(context):
    text = context.lower()

    built_terms = [
        "built area",
        "built surface",
        "built size",
        "constructed area",
        "construction area",
        "living area",
        "living space",
        "habitable",
        "surface habitable",
        "superficie habitable",
        "surface construite",
        "superficie construite",
        "covered area",
        "internal area"
    ]

    plot_terms = [
        "plot area",
        "plot size",
        "plot surface",
        "land area",
        "land size",
        "land surface",
        "lot size",
        "lot area",
        "terrain",
        "surface terrain",
        "superficie terrain",
        "surface du terrain",
        "superficie du terrain",
        "parcelle"
    ]

    built_score = sum(
        1 for term in built_terms
        if term in text
    )

    plot_score = sum(
        1 for term in plot_terms
        if term in text
    )

    if built_score > plot_score and built_score > 0:
        return "built"

    if plot_score > built_score and plot_score > 0:
        return "plot"

    return "unknown"


def structured_area_candidates(json_data):
    results = []

    area_keys = [
        "floorsize",
        "lotsize",
        "area",
        "surface",
        "size",
        "landarea",
        "livingarea",
        "floorarea"
    ]

    for item in walk_json(json_data):

        for key, raw in item.items():

            key_lower = str(key).lower()

            if key_lower not in area_keys:
                continue

            original_raw = raw

            if isinstance(raw, dict):
                raw = (
                    raw.get("value")
                    or raw.get("minValue")
                    or raw.get("maxValue")
                )

            if isinstance(raw, list):
                continue

            value = safe_int(raw)
            value = valid_area(value)

            if not value:
                continue

            area_type = "unknown"

            if key_lower in [
                "floorsize",
                "livingarea",
                "floorarea"
            ]:
                area_type = "built"

            elif key_lower in [
                "lotsize",
                "landarea"
            ]:
                area_type = "plot"

            results.append({
                "value": value,
                "type": area_type,
                "source": "structured_data",
                "field": str(key),
                "raw": clean_text(original_raw)[:200]
            })

    return results


def resolve_areas(text, json_data):
    mentions = get_area_mentions(text)

    candidates = []

    for mention in mentions:

        area_type = classify_area_context(
            mention["context"]
        )

        candidates.append({
            "value": mention["value"],
            "type": area_type,
            "source": "visible_text",
            "context": mention["context"]
        })

    candidates.extend(
        structured_area_candidates(json_data)
    )

    built_values = []
    plot_values = []
    unknown_values = []

    for candidate in candidates:

        value = candidate["value"]
        area_type = candidate["type"]

        if area_type == "built":
            built_values.append(value)

        elif area_type == "plot":
            plot_values.append(value)

        else:
            unknown_values.append(value)

    built_values = unique_values(built_values)
    plot_values = unique_values(plot_values)
    unknown_values = unique_values(unknown_values)

    built_area = None
    plot_area = None

    built_confidence = "none"
    plot_confidence = "none"

    if len(built_values) == 1:
        built_area = built_values[0]
        built_confidence = "high"

    elif len(built_values) > 1:
        built_confidence = "ambiguous"

    if len(plot_values) == 1:
        plot_area = plot_values[0]
        plot_confidence = "high"

    elif len(plot_values) > 1:
        plot_confidence = "ambiguous"

    return {
        "built_area_m2": built_area,
        "built_area_confidence": built_confidence,

        "plot_area_m2": plot_area,
        "plot_area_confidence": plot_confidence,

        "built_candidates_m2": built_values,
        "plot_candidates_m2": plot_values,
        "unclassified_candidates_m2": unknown_values,

        "all_candidates": candidates
    }


# =========================================================
# PROPERTY DETAILS
# =========================================================

def extract_bedrooms(text):
    patterns = [
        r"\b(\d{1,2})\s*bedrooms?\b",
        r"\b(\d{1,2})\s*beds?\b",
        r"\b(\d{1,2})\s*chambres?\b",
        r"\b(\d{1,2})\s*غرف(?:ة)?\b"
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE
        )

        if match:
            value = int(match.group(1))

            if 1 <= value <= 30:
                return value

    return None


def extract_bathrooms(text):
    patterns = [
        r"\b(\d{1,2})\s*bathrooms?\b",
        r"\b(\d{1,2})\s*baths?\b",
        r"\b(\d{1,2})\s*salles?\s+de\s+bain\b",
        r"\b(\d{1,2})\s*حمامات?\b"
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE
        )

        if match:
            value = int(match.group(1))

            if 1 <= value <= 30:
                return value

    return None


def detect_property_type(text):
    text = text.lower()

    types = [
        ("penthouse", "Penthouse"),
        ("duplex", "Duplex"),
        ("villa", "Villa"),
        ("riad", "Riad"),
        ("apartment", "Apartment"),
        ("appartement", "Apartment"),
        ("flat", "Apartment"),
        ("studio", "Studio"),
        ("house", "House"),
        ("maison", "House"),
        ("terrain", "Land"),
        ("land", "Land")
    ]

    for keyword, label in types:
        if keyword in text:
            return label

    return None


# =========================================================
# LOCATION
# =========================================================

def detect_city(text):
    text = text.lower()

    cities = [
        (["marrakech", "marrakesh"], "Marrakech"),
        (["casablanca"], "Casablanca"),
        (["rabat"], "Rabat"),
        (["tangier", "tanger"], "Tangier"),
        (["agadir"], "Agadir"),
        (["fes", "fez"], "Fes"),
        (["meknes"], "Meknes"),
        (["tetouan"], "Tetouan"),
        (["essaouira"], "Essaouira"),
        (["kenitra"], "Kenitra"),
        (["el jadida"], "El Jadida"),
        (["oujda"], "Oujda"),
        (["mohammedia"], "Mohammedia")
    ]

    for keywords, city in cities:

        for keyword in keywords:

            if keyword in text:
                return city

    return None


def detect_micro_location(text):
    text = text.lower()

    locations = [
        (
            [
                "route d'amizmiz",
                "route d’amezmiz",
                "route d'amezmiz",
                "route amizmiz",
                "route amezmiz",
                "amizmiz road"
            ],
            "Route d'Amizmiz"
        ),
        (
            [
                "route de l'ourika",
                "route de l’ourika",
                "route ourika",
                "ourika road"
            ],
            "Route de l'Ourika"
        ),
        (["palmeraie"], "Palmeraie"),
        (["hivernage"], "Hivernage"),
        (["gueliz", "guéliz"], "Gueliz"),
        (["agdal"], "Agdal"),
        (["targa"], "Targa"),
        (["amelkis"], "Amelkis"),
        (["al maaden"], "Al Maaden")
    ]

    for keywords, location in locations:

        for keyword in keywords:

            if keyword in text:
                return location

    return None


def detect_development(text):
    developments = [
        "Botanik Garden",
        "Argan Golf Resort",
        "Noria Golf",
        "Amelkis",
        "Al Maaden"
    ]

    lower = text.lower()

    for development in developments:

        if development.lower() in lower:
            return development

    return None


# =========================================================
# PRICE PER M2
# =========================================================

def calculate_price_per_m2(price, area):
    if not price or not area:
        return None

    try:
        return round(price / area)

    except (TypeError, ZeroDivisionError):
        return None


# =========================================================
# MARKET BENCHMARK DATA
# =========================================================

def load_market_benchmarks():
    try:
        base_dir = os.path.dirname(
            os.path.abspath(__file__)
        )

        path = os.path.join(
            base_dir,
            "data",
            "market_benchmarks.json"
        )

        with open(
            path,
            "r",
            encoding="utf-8"
        ) as file:
            return json.load(file)

    except Exception:
        return {
            "version": "1.0",
            "currency": "MAD",
            "benchmarks": []
        }


def find_market_benchmark(
    city,
    micro_location,
    property_type
):
    data = load_market_benchmarks()

    benchmarks = data.get(
        "benchmarks",
        []
    )

    best = None
    best_score = -1

    for benchmark in benchmarks:

        score = 0

        benchmark_city = benchmark.get("city")
        benchmark_location = benchmark.get("micro_location")
        benchmark_type = benchmark.get("property_type")

        if city and benchmark_city:

            if (
                city.lower()
                == benchmark_city.lower()
            ):
                score += 4

            else:
                continue

        if micro_location and benchmark_location:

            if (
                micro_location.lower()
                == benchmark_location.lower()
            ):
                score += 5

        if property_type and benchmark_type:

            if (
                property_type.lower()
                == benchmark_type.lower()
            ):
                score += 3

        if score > best_score:
            best = benchmark
            best_score = score

    if best_score < 4:
        return None

    return best


# =========================================================
# MARKET ANALYSIS
# =========================================================

def analyse_market(
    price,
    built_area,
    benchmark
):
    # Critical rule:
    # No built area = no price/m2 market verdict.

    if not price:
        return {
            "available": False,
            "status": "PRICE REQUIRED",
            "reason": "Price is not confirmed.",
            "listing_mad_m2": None,
            "benchmark_mad_m2": None,
            "difference_percent": None,
            "market_position": None
        }

    if not built_area:
        return {
            "available": False,
            "status": "AREA REQUIRED",
            "reason": "Built area is not confirmed.",
            "listing_mad_m2": None,
            "benchmark_mad_m2": (
                benchmark.get("benchmark_mad_m2")
                if benchmark
                else None
            ),
            "difference_percent": None,
            "market_position": None
        }

    listing_m2 = calculate_price_per_m2(
        price,
        built_area
    )

    if not benchmark:
        return {
            "available": False,
            "status": "BENCHMARK REQUIRED",
            "reason": "Suitable local market benchmark unavailable.",
            "listing_mad_m2": listing_m2,
            "benchmark_mad_m2": None,
            "difference_percent": None,
            "market_position": None
        }

    benchmark_m2 = benchmark.get(
        "benchmark_mad_m2"
    )

    if not benchmark_m2:
        return {
            "available": False,
            "status": "BENCHMARK REQUIRED",
            "reason": "Benchmark price per m2 unavailable.",
            "listing_mad_m2": listing_m2,
            "benchmark_mad_m2": None,
            "difference_percent": None,
            "market_position": None
        }

    difference = round(
        (
            (listing_m2 - benchmark_m2)
            / benchmark_m2
        ) * 100,
        1
    )

    if difference <= -15:
        position = "Potentially below local benchmark"

    elif difference <= -5:
        position = "Below local benchmark"

    elif difference < 5:
        position = "Near local benchmark"

    elif difference < 15:
        position = "Above local benchmark"

    else:
        position = "Well above local benchmark"

    return {
        "available": True,
        "status": "MARKET ANALYSIS AVAILABLE",

        "listing_mad_m2": listing_m2,
        "benchmark_mad_m2": benchmark_m2,

        "difference_percent": difference,
        "market_position": position,

        "benchmark_source": benchmark.get("source"),
        "benchmark_date": benchmark.get("source_date"),
        "sample_size": benchmark.get("sample_size"),
        "benchmark_confidence": benchmark.get("confidence"),

        "comparison_basis": "built_area"
    }


# =========================================================
# RADAR SCORE
# =========================================================

def calculate_radar_score(market_analysis):
    """
    Radar Score measures the property's price position.

    It is NOT data confidence.

    No valid market analysis = no Radar Score.
    """

    if not market_analysis.get("available"):
        return None

    difference = market_analysis.get(
        "difference_percent"
    )

    if difference is None:
        return None

    # Price well below benchmark.
    if difference <= -25:
        return 95

    if difference <= -20:
        return 90

    if difference <= -15:
        return 85

    if difference <= -10:
        return 80

    if difference <= -5:
        return 72

    # Around benchmark.
    if difference < 5:
        return 65

    # Above benchmark.
    if difference < 10:
        return 55

    if difference < 15:
        return 45

    if difference < 20:
        return 35

    return 25


# =========================================================
# DATA CONFIDENCE
# =========================================================

def calculate_data_confidence(
    price_result,
    property_type,
    city,
    micro_location,
    bedrooms,
    bathrooms,
    built_area,
    plot_area
):
    score = 0

    price = price_result["value"]

    if price:

        confidence = price_result["confidence"]

        if confidence == "high":
            score += 25

        elif confidence == "medium":
            score += 18

        else:
            score += 10

    if property_type:
        score += 10

    if city:
        score += 10

    if micro_location:
        score += 10

    if bedrooms:
        score += 5

    if bathrooms:
        score += 5

    if built_area:
        score += 25

    if plot_area:
        score += 10

    return min(score, 100)


# =========================================================
# VERIFICATION STATUS
# =========================================================

def get_verification_status(
    price_result,
    built_area
):
    price = price_result["value"]
    confidence = price_result["confidence"]

    if (
        price
        and confidence in ["high", "medium"]
        and built_area
    ):
        return "PROPERTY DATA VERIFIED"

    if (
        price
        and confidence in ["high", "medium"]
    ):
        return "PRICE VERIFIED - AREA REQUIRED"

    if price:
        return "PRICE DETECTED - REVIEW REQUIRED"

    return "PROPERTY DATA REQUIRES REVIEW"


# =========================================================
# DISPLAY LOGIC
# =========================================================

def build_radar_status(
    verification_status,
    market_analysis
):
    if market_analysis.get("available"):
        return market_analysis.get(
            "market_position"
        )

    status = market_analysis.get("status")

    if status == "AREA REQUIRED":
        return "PRICE VERIFIED - AREA REQUIRED"

    if status == "BENCHMARK REQUIRED":
        return "MARKET BENCHMARK UNAVAILABLE"

    if status == "PRICE REQUIRED":
        return "PRICE REQUIRED"

    return verification_status


# =========================================================
# UNIVERSAL SCANNER
# =========================================================

@app.route("/api/scan", methods=["POST"])
def scan_property():

    payload = request.get_json(
        silent=True
    ) or {}

    url = clean_text(
        payload.get("url")
    )

    if not url:
        return jsonify({
            "success": False,
            "error": "No property URL supplied."
        }), 400

    if not url.startswith(
        ("http://", "https://")
    ):
        return jsonify({
            "success": False,
            "error": "Invalid property URL."
        }), 400

    try:

        headers = {
            "User-Agent": (
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/138.0.0.0 "
                "Safari/537.36"
            ),

            "Accept": (
                "text/html,"
                "application/xhtml+xml,"
                "application/xml;q=0.9,"
                "*/*;q=0.8"
            ),

            "Accept-Language": (
                "en-GB,en;q=0.9,"
                "fr;q=0.8,"
                "ar;q=0.7"
            ),

            "Cache-Control": "no-cache"
        }

        response = requests.get(
            url,
            headers=headers,
            timeout=20,
            allow_redirects=True
        )

        response.raise_for_status()

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        # -----------------------------------------
        # REMOVE NON-CONTENT
        # -----------------------------------------

        for tag in soup(
            ["style", "noscript", "svg"]
        ):
            tag.decompose()

        # -----------------------------------------
        # TITLE
        # -----------------------------------------

        title = ""

        if soup.title:
            title = clean_text(
                soup.title.get_text(
                    " ",
                    strip=True
                )
            )

        og_title = get_meta(
            soup,
            prop="og:title"
        )

        if og_title:
            title = og_title

        # -----------------------------------------
        # DESCRIPTION
        # -----------------------------------------

        description = (
            get_meta(
                soup,
                name="description"
            )
            or
            get_meta(
                soup,
                prop="og:description"
            )
        )

        # -----------------------------------------
        # PAGE TEXT
        # -----------------------------------------

        page_text = normalise_text(
            soup.get_text(
                " ",
                strip=True
            )
        )

        json_ld = get_json_ld(soup)

        combined_text = normalise_text(
            " ".join([
                title,
                description,
                page_text
            ])
        )

        # =================================================
        # PRICE
        # =================================================

        structured_prices = (
            extract_structured_prices(
                json_ld
            )
        )

        meta_prices = (
            extract_meta_prices(
                soup
            )
        )

        visible_prices = (
            extract_visible_prices(
                combined_text
            )
        )

        price_result = choose_price(
            structured_prices,
            meta_prices,
            visible_prices
        )

        price = price_result["value"]

        # =================================================
        # AREA
        # =================================================

        area_result = resolve_areas(
            combined_text,
            json_ld
        )

        built_area = area_result[
            "built_area_m2"
        ]

        plot_area = area_result[
            "plot_area_m2"
        ]

        # =================================================
        # DETAILS
        # =================================================

        property_type = detect_property_type(
            combined_text
        )

        bedrooms = extract_bedrooms(
            combined_text
        )

        bathrooms = extract_bathrooms(
            combined_text
        )

        city = detect_city(
            combined_text
        )

        micro_location = detect_micro_location(
            combined_text
        )

        development = detect_development(
            combined_text
        )

        # =================================================
        # PRICE / M2
        # =================================================

        price_per_built_m2 = (
            calculate_price_per_m2(
                price,
                built_area
            )
        )

        price_per_plot_m2 = (
            calculate_price_per_m2(
                price,
                plot_area
            )
        )

        # =================================================
        # MARKET BENCHMARK
        # =================================================

        benchmark = find_market_benchmark(
            city,
            micro_location,
            property_type
        )

        # =================================================
        # MARKET ANALYSIS
        # =================================================

        market_analysis = analyse_market(
            price,
            built_area,
            benchmark
        )

        # =================================================
        # DATA CONFIDENCE
        # =================================================

        data_confidence = (
            calculate_data_confidence(
                price_result,
                property_type,
                city,
                micro_location,
                bedrooms,
                bathrooms,
                built_area,
                plot_area
            )
        )

        # =================================================
        # VERIFICATION
        # =================================================

        verification_status = (
            get_verification_status(
                price_result,
                built_area
            )
        )

        # =================================================
        # RADAR SCORE
        # =================================================

        radar_score = calculate_radar_score(
            market_analysis
        )

        radar_status = build_radar_status(
            verification_status,
            market_analysis
        )

        # =================================================
        # RESPONSE
        # =================================================

        return jsonify({

            "success": True,

            "scanner": {
                "version": "2.1",
                "mode": "universal",
                "domain": get_domain(url)
            },

            "source": {
                "url": url,
                "final_url": response.url,
                "title": title,
                "description": description
            },

            "property": {

                "property_type": property_type,

                "city": city,

                "micro_location": micro_location,

                "development": development,

                "bedrooms": bedrooms,

                "bathrooms": bathrooms,

                "price_mad": price,

                "price_confidence":
                    price_result["confidence"],

                "price_source":
                    price_result["source"],

                "built_area_m2":
                    built_area,

                "built_area_confidence":
                    area_result[
                        "built_area_confidence"
                    ],

                "plot_area_m2":
                    plot_area,

                "plot_area_confidence":
                    area_result[
                        "plot_area_confidence"
                    ],

                "price_per_built_m2_mad":
                    price_per_built_m2,

                "price_per_plot_m2_mad":
                    price_per_plot_m2
            },

            "market_analysis":
                market_analysis,

            "radar": {

                "verification_status":
                    verification_status,

                "status":
                    radar_status,

                "data_confidence":
                    data_confidence,

                "radar_score":
                    radar_score,

                "market_analysis_available":
                    market_analysis.get(
                        "available",
                        False
                    )
            },

            "unconfirmed_data": {

                "built_area_candidates_m2":
                    area_result[
                        "built_candidates_m2"
                    ],

                "plot_area_candidates_m2":
                    area_result[
                        "plot_candidates_m2"
                    ],

                "unclassified_area_candidates_m2":
                    area_result[
                        "unclassified_candidates_m2"
                    ]
            },

            "debug": {

                "price_candidates":
                    price_result[
                        "candidates"
                    ],

                "area_candidates":
                    area_result[
                        "all_candidates"
                    ]
            }
        })

    except requests.RequestException as error:

        return jsonify({
            "success": False,
            "error":
                "Property listing could not be retrieved.",
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


if __name__ == "__main__":
    app.run(debug=True)
