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
# HOME
# --------------------------------------------------

@app.route("/", methods=["GET"])
def home():
    return send_from_directory(".", "index.html")


# --------------------------------------------------
# BASIC HELPERS
# --------------------------------------------------

def clean_text(value):
    if value is None:
        return ""

    return re.sub(
        r"\s+",
        " ",
        str(value)
    ).strip()


def number_to_int(value):
    if value is None:
        return None

    text = clean_text(value)

    match = re.search(
        r"\d[\d\s.,]*",
        text
    )

    if not match:
        return None

    digits = re.sub(
        r"[^\d]",
        "",
        match.group(0)
    )

    if not digits:
        return None

    try:
        return int(digits)
    except ValueError:
        return None


# --------------------------------------------------
# META
# --------------------------------------------------

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

    if tag:
        return clean_text(
            tag.get("content", "")
        )

    return ""


# --------------------------------------------------
# JSON-LD
# --------------------------------------------------

def get_json_ld(soup):

    results = []

    scripts = soup.find_all(
        "script",
        attrs={
            "type": "application/ld+json"
        }
    )

    for script in scripts:

        raw = (
            script.string
            or script.get_text()
        )

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


# --------------------------------------------------
# PRICE
# --------------------------------------------------

def extract_price_from_json(data):

    for item in walk_json(data):

        for key in [
            "price",
            "lowPrice",
            "highPrice"
        ]:

            if key not in item:
                continue

            value = number_to_int(
                item.get(key)
            )

            if (
                value
                and
                10000 <= value <= 1000000000
            ):
                return value

        offers = item.get("offers")

        if isinstance(offers, dict):

            value = number_to_int(
                offers.get("price")
            )

            if (
                value
                and
                10000 <= value <= 1000000000
            ):
                return value

    return None


def extract_price_from_text(text):

    patterns = [

        r"(\d{1,3}(?:[\s,.]\d{3})+)\s*(?:MAD|DH|DHS)",

        r"(?:MAD|DH|DHS)\s*(\d{1,3}(?:[\s,.]\d{3})+)",

        r"(\d{5,10})\s*(?:MAD|DH|DHS)",

        r"(\d{1,3}(?:[\s,.]\d{3})+)\s*درهم",

        r"(\d{5,10})\s*درهم"
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE
        )

        if match:

            value = number_to_int(
                match.group(1)
            )

            if (
                value
                and
                10000 <= value <= 1000000000
            ):
                return value

    return None


# --------------------------------------------------
# AREA HELPERS
# --------------------------------------------------

def valid_area(value):

    if value is None:
        return None

    try:
        value = int(value)

        if 10 <= value <= 100000:
            return value

    except (TypeError, ValueError):
        pass

    return None


def find_area_with_labels(
    text,
    labels
):

    for label in labels:

        patterns = [

            rf"{label}\s*[:\-]?\s*(\d{{2,5}})\s*(?:m²|m2|sqm)",

            rf"{label}.{{0,40}}?(\d{{2,5}})\s*(?:m²|m2|sqm)",

            rf"(\d{{2,5}})\s*(?:m²|m2|sqm).{{0,30}}?{label}"
        ]

        for pattern in patterns:

            match = re.search(
                pattern,
                text,
                re.IGNORECASE
            )

            if match:

                area = valid_area(
                    match.group(1)
                )

                if area:
                    return area

    return None


# --------------------------------------------------
# PLOT / LAND AREA
# --------------------------------------------------

def extract_plot_area(text):

    labels = [

        r"plot\s*surface",

        r"plot\s*area",

        r"plot\s*size",

        r"land\s*surface",

        r"land\s*area",

        r"land\s*size",

        r"lot\s*surface",

        r"lot\s*area",

        r"surface\s*terrain",

        r"superficie\s*terrain",

        r"terrain",

        r"parcelle"
    ]

    return find_area_with_labels(
        text,
        labels
    )


# --------------------------------------------------
# BUILT / LIVING AREA
# --------------------------------------------------

def extract_built_area(text):

    labels = [

        r"built\s*area",

        r"built\s*surface",

        r"constructed\s*area",

        r"construction\s*area",

        r"living\s*area",

        r"living\s*surface",

        r"habitable\s*area",

        r"surface\s*habitable",

        r"superficie\s*habitable",

        r"surface\s*construite",

        r"superficie\s*construite"
    ]

    return find_area_with_labels(
        text,
        labels
    )


# --------------------------------------------------
# GENERIC LISTING AREA
# --------------------------------------------------

def extract_listing_area(text):

    patterns = [

        r"\b(\d{2,5})\s*m²\b",

        r"\b(\d{2,5})\s*m2\b",

        r"\b(\d{2,5})\s*sqm\b",

        r"\b(\d{2,5})\s*م²\b"
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE
        )

        if match:

            area = valid_area(
                match.group(1)
            )

            if area:
                return area

    return None


# --------------------------------------------------
# STRUCTURED AREA
# --------------------------------------------------

def extract_structured_area(json_data):

    for item in walk_json(json_data):

        for key in [
            "floorSize",
            "area",
            "surface",
            "size"
        ]:

            if key not in item:
                continue

            value = item.get(key)

            if isinstance(value, dict):

                candidate = (
                    value.get("value")
                    or
                    value.get("minValue")
                    or
                    value.get("maxValue")
                )

            else:

                candidate = value

            number = number_to_int(
                candidate
            )

            area = valid_area(number)

            if area:
                return area

    return None


# --------------------------------------------------
# BEDROOMS
# --------------------------------------------------

def extract_bedrooms(text):

    patterns = [

        r"(\d+)\s*bedrooms?",

        r"(\d+)\s*beds?\b",

        r"(\d+)\s*chambres?",

        r"(\d+)\s*غرف"
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE
        )

        if match:

            try:

                value = int(
                    match.group(1)
                )

                if 1 <= value <= 30:
                    return value

            except ValueError:
                pass

    return None


# --------------------------------------------------
# BATHROOMS
# --------------------------------------------------

def extract_bathrooms(text):

    patterns = [

        r"(\d+)\s*bathrooms?",

        r"(\d+)\s*baths?\b",

        r"(\d+)\s*salles?\s*de\s*bain"
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE
        )

        if match:

            try:

                value = int(
                    match.group(1)
                )

                if 1 <= value <= 30:
                    return value

            except ValueError:
                pass

    return None


# --------------------------------------------------
# ROOMS
# --------------------------------------------------

def extract_rooms(text):

    patterns = [

        r"(\d+)\s*rooms?",

        r"(\d+)\s*pi[eè]ces?"
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE
        )

        if match:

            try:

                value = int(
                    match.group(1)
                )

                if 1 <= value <= 50:
                    return value

            except ValueError:
                pass

    return None


# --------------------------------------------------
# PROPERTY TYPE
# --------------------------------------------------

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


# --------------------------------------------------
# CITY
# --------------------------------------------------

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


# --------------------------------------------------
# MICRO LOCATION
# --------------------------------------------------

def detect_micro_location(text):

    locations = [

        (
            [
                "route d'amizmiz",
                "route d’amizmiz",
                "route amizmiz",
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

        (
            [
                "route de casablanca",
                "route casablanca"
            ],
            "Route de Casablanca"
        ),

        (
            [
                "hivernage"
            ],
            "Hivernage"
        ),

        (
            [
                "gueliz",
                "guéliz"
            ],
            "Gueliz"
        ),

        (
            [
                "palmeraie"
            ],
            "Palmeraie"
        ),

        (
            [
                "agdal"
            ],
            "Agdal"
        ),

        (
            [
                "targa"
            ],
            "Targa"
        )
    ]

    lower = text.lower()

    for keywords, label in locations:

        for keyword in keywords:

            if keyword in lower:
                return label

    return None


# --------------------------------------------------
# DEVELOPMENT
# --------------------------------------------------

def detect_development(text):

    known_developments = [

        "Botanik Garden",

        "Argan Golf Resort",

        "Noria Golf",

        "Amelkis",

        "Al Maaden"
    ]

    lower = text.lower()

    for development in known_developments:

        if development.lower() in lower:
            return development

    return None


# --------------------------------------------------
# PRICE / AREA CALCULATIONS
# --------------------------------------------------

def price_per_m2(
    price,
    area
):

    if not price or not area:
        return None

    try:

        return round(
            price / area
        )

    except (
        TypeError,
        ZeroDivisionError
    ):

        return None


# --------------------------------------------------
# AREA CLASSIFICATION
# --------------------------------------------------

def classify_areas(
    listing_area,
    built_area,
    plot_area,
    structured_area
):

    result = {
        "listing_area_m2":
            listing_area,

        "built_area_m2":
            built_area,

        "plot_area_m2":
            plot_area,

        "structured_area_m2":
            structured_area,

        "listing_area_type":
            "unconfirmed",

        "area_confidence":
            "low"
    }


    if (
        built_area
        and
        listing_area
        and
        built_area == listing_area
    ):

        result[
            "listing_area_type"
        ] = "built_area"

        result[
            "area_confidence"
        ] = "high"


    elif (
        plot_area
        and
        listing_area
        and
        plot_area == listing_area
    ):

        result[
            "listing_area_type"
        ] = "plot_area"

        result[
            "area_confidence"
        ] = "high"


    elif (
        built_area
        and
        plot_area
        and
        listing_area
    ):

        result[
            "listing_area_type"
        ] = "listing_area"

        result[
            "area_confidence"
        ] = "medium"


    elif plot_area and listing_area:

        if plot_area != listing_area:

            result[
                "listing_area_type"
            ] = "unconfirmed_area"

            result[
                "area_confidence"
            ] = "medium"


    elif built_area and listing_area:

        result[
            "listing_area_type"
        ] = "built_area"

        result[
            "area_confidence"
        ] = "medium"


    return result


# --------------------------------------------------
# MARKET BENCHMARKS
# --------------------------------------------------

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

    market_data = (
        load_market_benchmarks()
    )

    benchmarks = market_data.get(
        "benchmarks",
        []
    )

    for benchmark in benchmarks:

        if (
            city
            and
            benchmark.get("city")
            and
            city.lower()
            !=
            benchmark.get(
                "city"
            ).lower()
        ):
            continue


        if (
            property_type
            and
            benchmark.get(
                "property_type"
            )
            and
            property_type.lower()
            !=
            benchmark.get(
                "property_type"
            ).lower()
        ):
            continue


        if (
            micro_location
            and
            benchmark.get(
                "micro_location"
            )
            and
            micro_location.lower()
            ==
            benchmark.get(
                "micro_location"
            ).lower()
        ):

            return benchmark

    return None


# --------------------------------------------------
# SAFE MARKET ANALYSIS
# --------------------------------------------------

def analyse_market(
    property_data,
    benchmark
):

    if not benchmark:

        return {
            "available": False,
            "reason":
                "No suitable local benchmark found."
        }


    benchmark_m2 = benchmark.get(
        "benchmark_mad_m2"
    )


    if not benchmark_m2:

        return {
            "available": False,
            "reason":
                "Benchmark value unavailable."
        }


    built_area = property_data.get(
        "built_area_m2"
    )

    listing_area = property_data.get(
        "listing_area_m2"
    )

    listing_area_type = property_data.get(
        "listing_area_type"
    )


    valuation_area = None
    valuation_area_type = None


    if built_area:

        valuation_area = built_area

        valuation_area_type = (
            "built_area"
        )


    elif (
        listing_area
        and
        listing_area_type
        == "built_area"
    ):

        valuation_area = listing_area

        valuation_area_type = (
            "built_area"
        )


    if valuation_area is None:

        return {

            "available": False,

            "reason":
                "Benchmark comparison paused because built area is not confirmed.",

            "benchmark_mad_m2":
                benchmark_m2,

            "benchmark_type":
                benchmark.get(
                    "benchmark_type"
                ),

            "source":
                benchmark.get(
                    "source"
                ),

            "sample_size":
                benchmark.get(
                    "sample_size"
                ),

            "valuation_area":
                None,

            "valuation_area_type":
                None
        }


    listing_m2 = property_data.get(
        "price_per_built_m2_mad"
    )


    if not listing_m2:

        return {
            "available": False,
            "reason":
                "Price per built m² could not be calculated."
        }


    difference = round(
        (
            (
                listing_m2
                -
                benchmark_m2
            )
            /
            benchmark_m2
        )
        *
        100,
        1
    )


    if difference <= -15:

        position = (
            "Potentially below local asking benchmark"
        )

    elif difference <= -5:

        position = (
            "Below local asking benchmark"
        )

    elif difference < 5:

        position = (
            "Near local asking benchmark"
        )

    elif difference < 15:

        position = (
            "Above local asking benchmark"
        )

    else:

        position = (
            "Well above local asking benchmark"
        )


    return {

        "available": True,

        "benchmark_mad_m2":
            benchmark_m2,

        "benchmark_type":
            benchmark.get(
                "benchmark_type"
            ),

        "source":
            benchmark.get(
                "source"
            ),

        "source_date":
            benchmark.get(
                "source_date"
            ),

        "sample_size":
            benchmark.get(
                "sample_size"
            ),

        "valuation_area":
            valuation_area,

        "valuation_area_type":
            valuation_area_type,

        "listing_mad_m2":
            listing_m2,

        "difference_percent":
            difference,

        "market_position":
            position
    }


# --------------------------------------------------
# MAIN SCANNER
# --------------------------------------------------

@app.route(
    "/api/scan",
    methods=["POST"]
)
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
                "en-GB,en;q=0.9,"
                "fr;q=0.8"
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


        page_text = clean_text(
            soup.get_text(
                " ",
                strip=True
            )
        )


        json_ld = get_json_ld(
            soup
        )


        combined_text = clean_text(
            " ".join([
                title,
                description,
                page_text
            ])
        )


        # PRICE

        price = (

            extract_price_from_json(
                json_ld
            )

            or

            extract_price_from_text(
                combined_text
            )
        )


        # AREAS

        plot_area = extract_plot_area(
            combined_text
        )

        built_area = extract_built_area(
            combined_text
        )

        structured_area = (
            extract_structured_area(
                json_ld
            )
        )

        listing_area = (
            structured_area
            or
            extract_listing_area(
                combined_text
            )
        )


        area_data = classify_areas(

            listing_area,
            built_area,
            plot_area,
            structured_area
        )


        # PROPERTY DETAILS

        bedrooms = extract_bedrooms(
            combined_text
        )

        bathrooms = extract_bathrooms(
            combined_text
        )

        rooms = extract_rooms(
            combined_text
        )

        property_type = (
            detect_property_type(
                combined_text
            )
        )

        city = detect_city(
            combined_text
        )

        micro_location = (
            detect_micro_location(
                combined_text
            )
        )

        development = (
            detect_development(
                combined_text
            )
        )


        # PRICE PER M2

        price_per_listing_m2 = (
            price_per_m2(
                price,
                listing_area
            )
        )

        price_per_plot_m2 = (
            price_per_m2(
                price,
                plot_area
            )
        )

        price_per_built_m2 = (
            price_per_m2(
                price,
                built_area
            )
        )


        property_data = {

            "city":
                city,

            "micro_location":
                micro_location,

            "development":
                development,

            "property_type":
                property_type,

            "price_mad":
                price,

            "bedrooms":
                bedrooms,

            "bathrooms":
                bathrooms,

            "rooms":
                rooms,

            "listing_area_m2":
                listing_area,

            "listing_area_type":
                area_data[
                    "listing_area_type"
                ],

            "built_area_m2":
                built_area,

            "plot_area_m2":
                plot_area,

            "area_confidence":
                area_data[
                    "area_confidence"
                ],

            "price_per_listing_m2_mad":
                price_per_listing_m2,

            "price_per_built_m2_mad":
                price_per_built_m2,

            "price_per_plot_m2_mad":
                price_per_plot_m2
        }


        # BENCHMARK

        benchmark = (
            find_market_benchmark(
                city,
                micro_location,
                property_type
            )
        )


        market_analysis = (
            analyse_market(
                property_data,
                benchmark
            )
        )


        # DATA CONFIDENCE

        important_fields = [

            price,
            city,
            property_type,
            micro_location,
            listing_area,
            plot_area,
            bedrooms
        ]


        detected = sum(
            value is not None
            for value
            in important_fields
        )


        data_confidence = round(
            detected
            /
            len(
                important_fields
            )
            *
            100
        )


        # STATUS

        if market_analysis.get(
            "available"
        ):

            status = (
                market_analysis.get(
                    "market_position"
                )
            )

        elif (
            price
            and
            listing_area
        ):

            status = (
                "PROPERTY DATA VERIFIED"
            )

        else:

            status = (
                "REVIEW LISTING"
            )


        return jsonify({

            "success": True,

            "source": {

                "url":
                    url,

                "final_url":
                    response.url,

                "title":
                    title,

                "description":
                    description
            },

            "property":
                property_data,

            "market_analysis":
                market_analysis,

            "radar": {

                "status":
                    status,

                "data_confidence":
                    data_confidence,

                "radar_score":
                    None
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
