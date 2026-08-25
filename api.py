import os
import re
import json
import requests

from bs4 import BeautifulSoup
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS


app = Flask(__name__)
CORS(app)


@app.route("/", methods=["GET"])
def home():
    return send_from_directory(".", "index.html")


def clean_text(value):
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def digits_to_int(value):
    if value is None:
        return None

    text = str(value).replace("\xa0", " ").strip()

    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        try:
            return int(float(text))
        except ValueError:
            return None

    digits = re.sub(r"[^\d]", "", text)

    if not digits:
        return None

    try:
        return int(digits)
    except ValueError:
        return None


def valid_price(value):
    return isinstance(value, int) and 100000 <= value <= 500000000


def valid_area(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None

    if 10 <= value <= 100000:
        return value

    return None


def get_meta(soup, name=None, prop=None):
    tag = None

    if name:
        tag = soup.find("meta", attrs={"name": name})

    if not tag and prop:
        tag = soup.find("meta", attrs={"property": prop})

    if not tag:
        return ""

    return clean_text(tag.get("content", ""))


def get_json_ld(soup):
    results = []

    for script in soup.find_all(
        "script",
        attrs={"type": "application/ld+json"}
    ):
        raw = script.string or script.get_text()

        if not raw:
            continue

        try:
            parsed = json.loads(raw)

            if isinstance(parsed, list):
                results.extend(parsed)

            elif isinstance(parsed, dict):
                graph = parsed.get("@graph")

                if isinstance(graph, list):
                    results.extend(graph)

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


# ----------------------------
# PRICE
# ----------------------------

def structured_price_candidates(json_data):
    candidates = []

    for item in walk_json(json_data):
        for key in ["price", "lowPrice", "highPrice"]:

            if key not in item:
                continue

            value = digits_to_int(item.get(key))

            if valid_price(value):
                candidates.append({
                    "value": value,
                    "source": "structured_data",
                    "field": key
                })

    return candidates


def meta_price_candidates(soup):
    candidates = []

    possible_meta = [
        ("property", "product:price:amount"),
        ("property", "og:price:amount"),
        ("name", "price"),
        ("itemprop", "price")
    ]

    for attribute, value in possible_meta:

        tag = soup.find("meta", attrs={attribute: value})

        if not tag:
            continue

        number = digits_to_int(tag.get("content", ""))

        if valid_price(number):
            candidates.append({
                "value": number,
                "source": "meta",
                "field": value
            })

    return candidates


def visible_price_candidates(text):
    candidates = []

    patterns = [
        r"(\d{1,3}(?:[ \u00a0.,]\d{3})+)\s*(?:MAD|DHS?|DH)\b",
        r"\b(?:MAD|DHS?|DH)\s*(\d{1,3}(?:[ \u00a0.,]\d{3})+)",
        r"(\d{5,9})\s*(?:MAD|DHS?|DH)\b",
        r"(\d{1,3}(?:[ \u00a0.,]\d{3})+)\s*درهم",
        r"(\d{5,9})\s*درهم"
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):

            value = digits_to_int(match.group(1))

            if valid_price(value):
                candidates.append({
                    "value": value,
                    "source": "visible_text",
                    "field": "currency_price"
                })

    return candidates


def select_price(structured, metadata, visible):
    all_candidates = metadata + structured + visible

    if not all_candidates:
        return None, "none", "low", []

    counts = {}

    for candidate in all_candidates:
        value = candidate["value"]
        counts[value] = counts.get(value, 0) + 1

    confirmed = [
        value
        for value, count in counts.items()
        if count >= 2
    ]

    if confirmed:
        confirmed.sort(
            key=lambda value: (counts[value], -value),
            reverse=True
        )

        chosen = confirmed[0]

        return chosen, "multiple_sources", "high", all_candidates

    if metadata:
        return metadata[0]["value"], "meta", "medium", all_candidates

    if structured:
        return (
            structured[0]["value"],
            "structured_data",
            "medium",
            all_candidates
        )

    return visible[0]["value"], "visible_text", "low", all_candidates


# ----------------------------
# AREA
# ----------------------------

def parse_area_number(value):
    if not value:
        return None

    value = str(value).replace("\xa0", " ").strip()

    match = re.search(r"\d{1,5}", value)

    if not match:
        return None

    return valid_area(match.group())


def find_labelled_area(text, labels):
    for label in labels:

        patterns = [
            rf"{label}\s*[:\-]?\s*(\d{{2,5}})\s*(?:m²|m2|sqm|m\^2)",
            rf"{label}.{{0,40}}?(\d{{2,5}})\s*(?:m²|m2|sqm|m\^2)",
            rf"(\d{{2,5}})\s*(?:m²|m2|sqm|m\^2).{{0,35}}?{label}"
        ]

        for pattern in patterns:
            match = re.search(
                pattern,
                text,
                re.IGNORECASE
            )

            if match:
                area = valid_area(match.group(1))

                if area:
                    return area

    return None


def extract_built_area(text):
    labels = [
        r"built\s*area",
        r"built\s*surface",
        r"built\s*size",
        r"constructed\s*area",
        r"constructed\s*surface",
        r"construction\s*area",
        r"living\s*area",
        r"living\s*space",
        r"living\s*surface",
        r"habitable\s*area",
        r"habitable\s*surface",
        r"surface\s*habitable",
        r"superficie\s*habitable",
        r"surface\s*construite",
        r"superficie\s*construite"
    ]

    return find_labelled_area(text, labels)


def extract_plot_area(text):
    labels = [
        r"plot\s*area",
        r"plot\s*size",
        r"plot\s*surface",
        r"land\s*area",
        r"land\s*size",
        r"land\s*surface",
        r"lot\s*area",
        r"lot\s*size",
        r"surface\s*terrain",
        r"superficie\s*terrain",
        r"surface\s*du\s*terrain",
        r"superficie\s*du\s*terrain",
        r"terrain"
    ]

    return find_labelled_area(text, labels)


def extract_generic_areas(text):
    results = []

    patterns = [
        r"\b(\d{2,5})\s*m²\b",
        r"\b(\d{2,5})\s*m2\b",
        r"\b(\d{2,5})\s*sqm\b",
        r"\b(\d{2,5})\s*m\^2\b"
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):

            area = valid_area(match.group(1))

            if area and area not in results:
                results.append(area)

    return results[:20]


def structured_area_candidates(json_data):
    results = []

    for item in walk_json(json_data):

        for key in [
            "floorSize",
            "floor_size",
            "area",
            "surface",
            "size",
            "lotSize"
        ]:

            if key not in item:
                continue

            raw = item.get(key)

            if isinstance(raw, dict):
                raw = (
                    raw.get("value")
                    or raw.get("minValue")
                    or raw.get("maxValue")
                )

            value = parse_area_number(raw)

            if value and value not in results:
                results.append(value)

    return results


def extract_contextual_areas(text):
    """
    Returns every area together with nearby text.

    This helps us understand whether 450 m² means
    land, built area, terrace, garden, etc.
    """

    results = []

    pattern = re.compile(
        r"\b(\d{2,5})\s*(?:m²|m2|sqm|m\^2)\b",
        re.IGNORECASE
    )

    for match in pattern.finditer(text):

        value = valid_area(match.group(1))

        if not value:
            continue

        start = max(0, match.start() - 80)
        end = min(len(text), match.end() + 80)

        context = clean_text(text[start:end])

        item = {
            "value": value,
            "context": context
        }

        if item not in results:
            results.append(item)

    return results[:20]


def classify_area_from_context(context):
    lower = context.lower()

    plot_words = [
        "plot",
        "land",
        "terrain",
        "parcelle",
        "lot",
        "superficie terrain",
        "surface terrain"
    ]

    built_words = [
        "built",
        "constructed",
        "construction",
        "living",
        "habitable",
        "construite",
        "habitable"
    ]

    plot_score = sum(
        1 for word in plot_words
        if word in lower
    )

    built_score = sum(
        1 for word in built_words
        if word in lower
    )

    if plot_score > built_score and plot_score > 0:
        return "plot"

    if built_score > plot_score and built_score > 0:
        return "built"

    return "unknown"


def resolve_areas(text, json_data):
    built_area = extract_built_area(text)
    plot_area = extract_plot_area(text)

    contextual = extract_contextual_areas(text)

    for item in contextual:
        classification = classify_area_from_context(
            item["context"]
        )

        if classification == "built" and not built_area:
            built_area = item["value"]

        elif classification == "plot" and not plot_area:
            plot_area = item["value"]

    generic = extract_generic_areas(text)
    structured = structured_area_candidates(json_data)

    return {
        "built_area_m2": built_area,
        "plot_area_m2": plot_area,
        "generic_candidates_m2": generic,
        "structured_candidates_m2": structured,
        "contextual_candidates": contextual
    }


# ----------------------------
# PROPERTY DETAILS
# ----------------------------

def extract_bedrooms(text):
    patterns = [
        r"(\d+)\s*bedrooms?",
        r"(\d+)\s*beds?\b",
        r"(\d+)\s*chambres?"
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)

        if match:
            value = int(match.group(1))

            if 1 <= value <= 30:
                return value

    return None


def extract_bathrooms(text):
    patterns = [
        r"(\d+)\s*bathrooms?",
        r"(\d+)\s*baths?\b",
        r"(\d+)\s*salles?\s*de\s*bain"
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)

        if match:
            value = int(match.group(1))

            if 1 <= value <= 30:
                return value

    return None


def detect_property_type(text):
    types = [
        ("villa", "Villa"),
        ("riad", "Riad"),
        ("apartment", "Apartment"),
        ("appartement", "Apartment"),
        ("penthouse", "Penthouse"),
        ("duplex", "Duplex"),
        ("studio", "Studio"),
        ("house", "House"),
        ("maison", "House"),
        ("land", "Land"),
        ("terrain", "Land")
    ]

    lower = text.lower()

    for keyword, label in types:
        if keyword in lower:
            return label

    return None


def detect_city(text):
    cities = [
        ("marrakech", "Marrakech"),
        ("marrakesh", "Marrakech"),
        ("casablanca", "Casablanca"),
        ("rabat", "Rabat"),
        ("tangier", "Tangier"),
        ("tanger", "Tangier"),
        ("agadir", "Agadir"),
        ("fes", "Fes"),
        ("fez", "Fes"),
        ("meknes", "Meknes"),
        ("tetouan", "Tetouan"),
        ("essaouira", "Essaouira"),
        ("kenitra", "Kenitra"),
        ("el jadida", "El Jadida"),
        ("oujda", "Oujda"),
        ("mohammedia", "Mohammedia")
    ]

    lower = text.lower()

    for keyword, city in cities:
        if keyword in lower:
            return city

    return None


def detect_micro_location(text):
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
        (["hivernage"], "Hivernage"),
        (["gueliz", "guéliz"], "Gueliz"),
        (["palmeraie"], "Palmeraie"),
        (["agdal"], "Agdal"),
        (["targa"], "Targa")
    ]

    lower = text.lower()

    for keywords, label in locations:
        for keyword in keywords:
            if keyword in lower:
                return label

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


def calculate_price_per_m2(price, area):
    if not price or not area:
        return None

    try:
        return round(price / area)
    except (TypeError, ZeroDivisionError):
        return None


# ----------------------------
# MARKET BENCHMARK
# ----------------------------

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

        with open(path, "r", encoding="utf-8") as file:
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

    for benchmark in data.get("benchmarks", []):

        benchmark_city = benchmark.get("city")
        benchmark_location = benchmark.get("micro_location")
        benchmark_type = benchmark.get("property_type")

        if (
            city
            and benchmark_city
            and city.lower() != benchmark_city.lower()
        ):
            continue

        if (
            property_type
            and benchmark_type
            and property_type.lower() != benchmark_type.lower()
        ):
            continue

        if (
            micro_location
            and benchmark_location
            and micro_location.lower() == benchmark_location.lower()
        ):
            return benchmark

    return None


def analyse_market(property_data, benchmark):
    if not benchmark:
        return {
            "available": False,
            "reason": "No suitable local benchmark found."
        }

    benchmark_m2 = benchmark.get("benchmark_mad_m2")
    built_area = property_data.get("built_area_m2")
    price = property_data.get("price_mad")

    if not built_area:
        return {
            "available": False,
            "reason": "Built area must be confirmed before market comparison.",
            "benchmark_mad_m2": benchmark_m2,
            "benchmark_type": benchmark.get("benchmark_type"),
            "source": benchmark.get("source"),
            "sample_size": benchmark.get("sample_size")
        }

    if not price or not benchmark_m2:
        return {
            "available": False,
            "reason": "Insufficient verified data for market comparison."
        }

    listing_m2 = calculate_price_per_m2(
        price,
        built_area
    )

    difference = round(
        ((listing_m2 - benchmark_m2) / benchmark_m2) * 100,
        1
    )

    if difference <= -15:
        position = "Potentially below local asking benchmark"

    elif difference <= -5:
        position = "Below local asking benchmark"

    elif difference < 5:
        position = "Near local asking benchmark"

    elif difference < 15:
        position = "Above local asking benchmark"

    else:
        position = "Well above local asking benchmark"

    return {
        "available": True,
        "benchmark_mad_m2": benchmark_m2,
        "benchmark_type": benchmark.get("benchmark_type"),
        "source": benchmark.get("source"),
        "source_date": benchmark.get("source_date"),
        "sample_size": benchmark.get("sample_size"),
        "valuation_area_m2": built_area,
        "valuation_area_type": "built_area",
        "listing_mad_m2": listing_m2,
        "difference_percent": difference,
        "market_position": position
    }


# ----------------------------
# API
# ----------------------------

@app.route("/api/scan", methods=["POST"])
def scan_property():

    payload = request.get_json(silent=True) or {}
    url = clean_text(payload.get("url"))

    if not url:
        return jsonify({
            "success": False,
            "error": "No property URL supplied."
        }), 400

    if not url.startswith(("http://", "https://")):
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
            "Accept-Language": "en-GB,en;q=0.9,fr;q=0.8"
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

        title = ""

        if soup.title:
            title = clean_text(
                soup.title.get_text(" ", strip=True)
            )

        og_title = get_meta(
            soup,
            prop="og:title"
        )

        if og_title:
            title = og_title

        description = (
            get_meta(soup, name="description")
            or get_meta(soup, prop="og:description")
        )

        page_text = clean_text(
            soup.get_text(" ", strip=True)
        )

        json_ld = get_json_ld(soup)

        combined_text = clean_text(
            " ".join([
                title,
                description,
                page_text
            ])
        )

        # PRICE

        structured_prices = structured_price_candidates(
            json_ld
        )

        metadata_prices = meta_price_candidates(
            soup
        )

        visible_prices = visible_price_candidates(
            combined_text
        )

        (
            price,
            price_source,
            price_confidence,
            price_candidates
        ) = select_price(
            structured_prices,
            metadata_prices,
            visible_prices
        )

        # AREA

        area_data = resolve_areas(
            combined_text,
            json_ld
        )

        built_area = area_data[
            "built_area_m2"
        ]

        plot_area = area_data[
            "plot_area_m2"
        ]

        # DETAILS

        bedrooms = extract_bedrooms(
            combined_text
        )

        bathrooms = extract_bathrooms(
            combined_text
        )

        property_type = detect_property_type(
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

        # PRICE PER M2

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

        property_data = {
            "city": city,
            "micro_location": micro_location,
            "development": development,
            "property_type": property_type,

            "price_mad": price,
            "price_source": price_source,
            "price_confidence": price_confidence,

            "bedrooms": bedrooms,
            "bathrooms": bathrooms,

            "built_area_m2": built_area,
            "plot_area_m2": plot_area,

            "price_per_built_m2_mad":
                price_per_built_m2,

            "price_per_plot_m2_mad":
                price_per_plot_m2,

            "area_candidates_m2":
                area_data["generic_candidates_m2"],

            "structured_area_candidates_m2":
                area_data["structured_candidates_m2"]
        }

        # MARKET

        benchmark = find_market_benchmark(
            city,
            micro_location,
            property_type
        )

        market_analysis = analyse_market(
            property_data,
            benchmark
        )

        # CONFIDENCE

        score = 0

        if price:
            if price_confidence == "high":
                score += 25
            elif price_confidence == "medium":
                score += 18
            else:
                score += 10

        if city:
            score += 10

        if micro_location:
            score += 15

        if property_type:
            score += 10

        if bedrooms:
            score += 5

        if built_area:
            score += 25

        if plot_area:
            score += 10

        data_confidence = min(
            score,
            100
        )

        if (
            price
            and price_confidence in ("high", "medium")
            and built_area
        ):
            verification_status = (
                "CORE PROPERTY DATA VERIFIED"
            )

        elif (
            price
            and price_confidence in ("high", "medium")
        ):
            verification_status = (
                "PRICE VERIFIED - AREA UNCONFIRMED"
            )

        else:
            verification_status = (
                "REVIEW PROPERTY DATA"
            )

        if market_analysis.get("available"):
            radar_status = market_analysis.get(
                "market_position"
            )
        else:
            radar_status = verification_status

        return jsonify({
            "success": True,

            "source": {
                "url": url,
                "final_url": response.url,
                "title": title,
                "description": description
            },

            "property": property_data,

            "market_analysis": market_analysis,

            "radar": {
                "status": radar_status,
                "verification_status":
                    verification_status,
                "data_confidence":
                    data_confidence,
                "radar_score": None
            },

            "debug": {
                "price_candidates":
                    price_candidates,

                "area_candidates":
                    area_data[
                        "contextual_candidates"
                    ],

                "generic_area_candidates_m2":
                    area_data[
                        "generic_candidates_m2"
                    ],

                "structured_area_candidates_m2":
                    area_data[
                        "structured_candidates_m2"
                    ]
            }
        })

    except requests.RequestException as error:
        return jsonify({
            "success": False,
            "error":
                "Property listing could not be retrieved.",
            "details": str(error)
        }), 502

    except Exception as error:
        return jsonify({
            "success": False,
            "error":
                "Property scan failed.",
            "details": str(error)
        }), 500


if __name__ == "__main__":
    app.run(debug=True)
