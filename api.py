import os
import re
import json
import requests

from bs4 import BeautifulSoup
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS


app = Flask(__name__)
CORS(app)


# --------------------------------------------------
# HOME PAGE
# --------------------------------------------------

@app.route("/", methods=["GET"])
def home():
    return send_from_directory(".", "index.html")


# --------------------------------------------------
# HELPERS
# --------------------------------------------------

def clean_text(text):
    return re.sub(r"\s+", " ", text or "").strip()


def extract_number(value):
    if not value:
        return None

    value = value.replace("\xa0", " ")

    numbers = re.findall(r"\d[\d\s.,]*", value)

    if not numbers:
        return None

    number = re.sub(r"[^\d]", "", numbers[0])

    try:
        return int(number)
    except ValueError:
        return None


def extract_price(text):
    patterns = [
        r"(\d[\d\s.,]{2,})\s*(?:MAD|DH|DHS)",
        r"(?:MAD|DH|DHS)\s*(\d[\d\s.,]{2,})",
        r"(\d[\d\s.,]{2,})\s*درهم"
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)

        if match:
            value = extract_number(match.group(1))

            if value and value >= 10000:
                return value

    return None


def extract_area(text):
    patterns = [
        r"(\d{2,5})\s*m²",
        r"(\d{2,5})\s*m2",
        r"(\d{2,5})\s*sqm",
        r"(\d{2,5})\s*م²"
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)

        if match:
            try:
                area = int(match.group(1))

                if 10 <= area <= 100000:
                    return area

            except ValueError:
                pass

    return None


def extract_bedrooms(text):
    patterns = [
        r"(\d+)\s*bedrooms?",
        r"(\d+)\s*beds?",
        r"(\d+)\s*chambres?",
        r"(\d+)\s*غرف"
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)

        if match:
            try:
                bedrooms = int(match.group(1))

                if 1 <= bedrooms <= 30:
                    return bedrooms

            except ValueError:
                pass

    return None


def detect_property_type(text):
    property_types = {
        "villa": "Villa",
        "apartment": "Apartment",
        "appartement": "Apartment",
        "riad": "Riad",
        "house": "House",
        "maison": "House",
        "land": "Land",
        "terrain": "Land",
        "studio": "Studio",
        "duplex": "Duplex",
        "penthouse": "Penthouse"
    }

    lower_text = text.lower()

    for keyword, label in property_types.items():
        if keyword in lower_text:
            return label

    return None


def detect_city(text):
    cities = [
        "Marrakech",
        "Casablanca",
        "Rabat",
        "Tangier",
        "Tanger",
        "Agadir",
        "Fes",
        "Fez",
        "Meknes",
        "Tetouan",
        "Essaouira",
        "Kenitra",
        "El Jadida",
        "Oujda",
        "Mohammedia"
    ]

    lower_text = text.lower()

    for city in cities:
        if city.lower() in lower_text:

            if city == "Tanger":
                return "Tangier"

            if city == "Fez":
                return "Fes"

            return city

    return None


def detect_micro_location(text):
    locations = {
        "route d'amizmiz": "Route d'Amizmiz",
        "route d’amizmiz": "Route d'Amizmiz",
        "amizmiz road": "Route d'Amizmiz",
        "route de l'ourika": "Route de l'Ourika",
        "route de l’ourika": "Route de l'Ourika",
        "ourika road": "Route de l'Ourika",
        "route de casa": "Route de Casablanca",
        "route de casablanca": "Route de Casablanca",
        "hivernage": "Hivernage",
        "gueliz": "Gueliz",
        "guéliz": "Gueliz",
        "palmeraie": "Palmeraie",
        "agdal": "Agdal"
    }

    lower_text = text.lower()

    for keyword, location in locations.items():
        if keyword in lower_text:
            return location

    return None


def calculate_price_per_m2(price, area):
    if not price or not area:
        return None

    try:
        return round(price / area)
    except (TypeError, ZeroDivisionError):
        return None


# --------------------------------------------------
# MARKET BENCHMARK DATA
# --------------------------------------------------

def load_market_benchmarks():
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))

        file_path = os.path.join(
            base_dir,
            "data",
            "market_benchmarks.json"
        )

        with open(
            file_path,
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
    market_data = load_market_benchmarks()

    benchmarks = market_data.get(
        "benchmarks",
        []
    )

    best_match = None
    best_score = -1

    for benchmark in benchmarks:

        score = 0

        benchmark_city = benchmark.get("city")
        benchmark_location = benchmark.get("micro_location")
        benchmark_type = benchmark.get("property_type")

        if city and benchmark_city:

            if city.lower() == benchmark_city.lower():
                score += 3
            else:
                continue

        if property_type and benchmark_type:

            if property_type.lower() == benchmark_type.lower():
                score += 3
            else:
                continue

        if micro_location and benchmark_location:

            if micro_location.lower() == benchmark_location.lower():
                score += 5

        if score > best_score:
            best_score = score
            best_match = benchmark

    return best_match


# --------------------------------------------------
# MARKET ANALYSIS
# --------------------------------------------------

def analyse_market(
    price,
    area,
    price_per_m2,
    benchmark
):
    if not benchmark:

        return {
            "available": False,
            "reason": "No suitable local market benchmark found."
        }

    benchmark_m2 = benchmark.get(
        "benchmark_mad_m2"
    )

    if not benchmark_m2:

        return {
            "available": False,
            "reason": "Benchmark does not contain a valid MAD/m² value."
        }

    result = {
        "available": True,

        "benchmark_mad_m2": benchmark_m2,

        "benchmark_type": benchmark.get(
            "benchmark_type"
        ),

        "source": benchmark.get(
            "source"
        ),

        "source_date": benchmark.get(
            "source_date"
        ),

        "sample_size": benchmark.get(
            "sample_size"
        ),

        "benchmark_confidence": benchmark.get(
            "confidence"
        ),

        "notes": benchmark.get(
            "notes"
        )
    }

    if not price_per_m2:

        result.update({
            "listing_mad_m2": None,
            "difference_percent": None,
            "market_position": "Insufficient property data",
            "estimated_value_mad": None,
            "estimated_value_low_mad": None,
            "estimated_value_high_mad": None,
            "negotiation_signal": "Review listing"
        })

        return result


    difference = (
        (price_per_m2 - benchmark_m2)
        / benchmark_m2
    ) * 100

    difference = round(
        difference,
        1
    )


    if difference <= -15:
        market_position = "Potentially undervalued"
        negotiation_signal = "Investigate opportunity"

    elif difference <= -5:
        market_position = "Below local asking benchmark"
        negotiation_signal = "Potential value"

    elif difference < 5:
        market_position = "Near local asking benchmark"
        negotiation_signal = "Fair asking range"

    elif difference < 15:
        market_position = "Above local asking benchmark"
        negotiation_signal = "Negotiate"

    else:
        market_position = "Well above local asking benchmark"
        negotiation_signal = "Strong negotiation case"


    estimated_value = None
    estimated_low = None
    estimated_high = None

    if area:

        estimated_value = round(
            area * benchmark_m2
        )

        estimated_low = round(
            estimated_value * 0.90
        )

        estimated_high = round(
            estimated_value * 1.10
        )


    result.update({

        "listing_mad_m2": price_per_m2,

        "difference_percent": difference,

        "market_position": market_position,

        "estimated_value_mad": estimated_value,

        "estimated_value_low_mad": estimated_low,

        "estimated_value_high_mad": estimated_high,

        "negotiation_signal": negotiation_signal
    })

    return result


# --------------------------------------------------
# RADAR SCORE
# --------------------------------------------------

def calculate_radar_score(
    price,
    area,
    city,
    property_type,
    micro_location,
    market_analysis
):
    score = 0

    if price:
        score += 15

    if area:
        score += 15

    if city:
        score += 10

    if property_type:
        score += 10

    if micro_location:
        score += 10

    if market_analysis.get("available"):
        score += 20

        difference = market_analysis.get(
            "difference_percent"
        )

        if difference is not None:

            if difference <= -15:
                score += 20

            elif difference <= -5:
                score += 16

            elif difference < 5:
                score += 12

            elif difference < 15:
                score += 7

            else:
                score += 2

    return min(
        round(score),
        100
    )


# --------------------------------------------------
# SCANNER
# --------------------------------------------------

@app.route("/api/scan", methods=["POST"])
def scan_property():

    data = request.get_json(
        silent=True
    ) or {}

    url = data.get(
        "url",
        ""
    ).strip()

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

            "Accept-Language":
                "en-GB,en;q=0.9,fr;q=0.8"
        }


        response = requests.get(
            url,
            headers=headers,
            timeout=15
        )

        response.raise_for_status()


        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )


        title = ""

        if soup.title:

            title = clean_text(
                soup.title.get_text(
                    " ",
                    strip=True
                )
            )


        description = ""

        description_tag = soup.find(
            "meta",
            attrs={
                "name": "description"
            }
        )

        if description_tag:

            description = clean_text(
                description_tag.get(
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


        page_text = clean_text(
            soup.get_text(
                " ",
                strip=True
            )
        )


        analysis_text = " ".join([
            title,
            description,
            page_text[:20000]
        ])


        price = extract_price(
            analysis_text
        )

        area = extract_area(
            analysis_text
        )

        bedrooms = extract_bedrooms(
            analysis_text
        )

        property_type = detect_property_type(
            analysis_text
        )

        city = detect_city(
            analysis_text
        )

        micro_location = detect_micro_location(
            analysis_text
        )


        price_per_m2 = calculate_price_per_m2(
            price,
            area
        )


        benchmark = find_market_benchmark(
            city,
            micro_location,
            property_type
        )


        market_analysis = analyse_market(
            price,
            area,
            price_per_m2,
            benchmark
        )


        detected_fields = 0

        for value in [
            price,
            area,
            bedrooms,
            property_type,
            city,
            micro_location
        ]:

            if value is not None:
                detected_fields += 1


        data_confidence = round(
            (detected_fields / 6) * 100
        )


        radar_score = calculate_radar_score(
            price,
            area,
            city,
            property_type,
            micro_location,
            market_analysis
        )


        if market_analysis.get("available"):

            radar_status = market_analysis.get(
                "market_position"
            )

        elif price and area:

            radar_status = (
                "Property extracted. "
                "Local benchmark required."
            )

        else:

            radar_status = "Review listing"


        return jsonify({

            "success": True,

            "source": {
                "url": url,
                "title": title,
                "description": description
            },

            "property": {
                "city": city,
                "micro_location": micro_location,
                "property_type": property_type,
                "price_mad": price,
                "area_m2": area,
                "bedrooms": bedrooms,
                "price_per_m2_mad": price_per_m2
            },

            "market_analysis":
                market_analysis,

            "radar": {
                "status": radar_status,
                "data_confidence":
                    data_confidence,
                "score":
                    radar_score
            },

            "content":
                page_text[:12000]
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


# --------------------------------------------------
# LOCAL DEVELOPMENT
# --------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True)
