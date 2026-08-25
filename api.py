import json
import math
import re
import requests

from bs4 import BeautifulSoup
from flask import Flask, request, jsonify
from flask_cors import CORS


app = Flask(__name__, static_folder=".", static_url_path="")
CORS(app)


# ---------------------------------------------------------
# BASIC ROUTES
# ---------------------------------------------------------

@app.route("/", methods=["GET"])
def home():
    return app.send_static_file("index.html")


# ---------------------------------------------------------
# TEXT HELPERS
# ---------------------------------------------------------

def clean_text(value):
    if value is None:
        return ""

    return re.sub(
        r"\s+",
        " ",
        str(value)
    ).strip()


def digits_to_int(value):
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


# ---------------------------------------------------------
# META DATA
# ---------------------------------------------------------

def get_meta(
    soup,
    name=None,
    prop=None
):
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
            tag.get(
                "content",
                ""
            )
        )

    return ""


# ---------------------------------------------------------
# JSON-LD
# ---------------------------------------------------------

def get_json_ld(soup):
    results = []

    scripts = soup.find_all(
        "script",
        attrs={
            "type":
            "application/ld+json"
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

            if isinstance(
                parsed,
                list
            ):
                results.extend(parsed)

            elif isinstance(
                parsed,
                dict
            ):

                graph = parsed.get(
                    "@graph"
                )

                if isinstance(
                    graph,
                    list
                ):
                    results.extend(
                        graph
                    )

                results.append(
                    parsed
                )

        except Exception:
            continue

    return results


def walk_json(value):

    if isinstance(
        value,
        dict
    ):

        yield value

        for child in value.values():
            yield from walk_json(
                child
            )

    elif isinstance(
        value,
        list
    ):

        for child in value:
            yield from walk_json(
                child
            )


# ---------------------------------------------------------
# PRICE
# ---------------------------------------------------------

def extract_price_from_json(data):

    possible_keys = [
        "price",
        "lowPrice",
        "highPrice"
    ]

    for item in walk_json(data):

        for key in possible_keys:

            if key not in item:
                continue

            value = digits_to_int(
                item.get(key)
            )

            if (
                value
                and
                10000
                <= value
                <= 1000000000
            ):
                return value

        offers = item.get(
            "offers"
        )

        if isinstance(
            offers,
            dict
        ):

            value = digits_to_int(
                offers.get(
                    "price"
                )
            )

            if (
                value
                and
                10000
                <= value
                <= 1000000000
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

            value = digits_to_int(
                match.group(1)
            )

            if (
                value
                and
                10000
                <= value
                <= 1000000000
            ):
                return value

    return None


# ---------------------------------------------------------
# AREA
# ---------------------------------------------------------

def extract_area_from_json(data):

    keys = [
        "floorSize",
        "area",
        "surface",
        "size"
    ]

    for item in walk_json(data):

        for key in keys:

            if key not in item:
                continue

            value = item.get(key)

            if isinstance(
                value,
                dict
            ):

                candidate = (
                    value.get("value")
                    or
                    value.get(
                        "minValue"
                    )
                    or
                    value.get(
                        "maxValue"
                    )
                )

            else:
                candidate = value

            number = digits_to_int(
                candidate
            )

            if (
                number
                and
                10
                <= number
                <= 100000
            ):
                return number

    return None


def extract_area_from_text(text):

    patterns = [
        r"(\d{2,5})\s*m²",
        r"(\d{2,5})\s*m2\b",
        r"(\d{2,5})\s*sqm\b",
        r"(\d{2,5})\s*م²"
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

                if (
                    10
                    <= value
                    <= 100000
                ):
                    return value

            except ValueError:
                pass

    return None


# ---------------------------------------------------------
# ROOMS
# ---------------------------------------------------------

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

                if (
                    1
                    <= value
                    <= 30
                ):
                    return value

            except ValueError:
                pass

    return None


def extract_rooms(text):

    patterns = [
        r"(\d+)\s*rooms?",
        r"(\d+)\s*pièces?",
        r"(\d+)\s*pieces?"
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

                if (
                    1
                    <= value
                    <= 50
                ):
                    return value

            except ValueError:
                pass

    return None


# ---------------------------------------------------------
# PROPERTY TYPE
# ---------------------------------------------------------

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


# ---------------------------------------------------------
# CITY
# ---------------------------------------------------------

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


# ---------------------------------------------------------
# MARRAKECH MICRO-LOCATIONS
# ---------------------------------------------------------

def detect_marrakech_location(text):

    lower = text.lower()

    locations = [
        (
            [
                "route amizmiz",
                "route d'amizmiz",
                "route d’amizmiz",
                "route de amizmiz"
            ],
            "Route d'Amizmiz",
            "South-West Marrakech"
        ),

        (
            [
                "route ourika",
                "route de l'ourika",
                "route de lourika",
                "route d'ourika",
                "route d’ourika"
            ],
            "Route de l'Ourika",
            "South Marrakech"
        ),

        (
            [
                "route casablanca",
                "route de casablanca"
            ],
            "Route de Casablanca",
            "North Marrakech"
        ),

        (
            [
                "route fes",
                "route de fes",
                "route de fès"
            ],
            "Route de Fès",
            "East Marrakech"
        ),

        (
            [
                "route tahanaout",
                "route de tahanaout"
            ],
            "Route de Tahanaout",
            "South Marrakech"
        ),

        (
            [
                "route ouarzazate",
                "route de ouarzazate",
                "route de ouarzazate"
            ],
            "Route de Ouarzazate",
            "South-East Marrakech"
        ),

        (
            [
                "hivernage"
            ],
            "Hivernage",
            "Central Marrakech"
        ),

        (
            [
                "gueliz",
                "guéliz"
            ],
            "Guéliz",
            "Central Marrakech"
        ),

        (
            [
                "palmeraie"
            ],
            "Palmeraie",
            "North-East Marrakech"
        ),

        (
            [
                "agdal"
            ],
            "Agdal",
            "South-Central Marrakech"
        ),

        (
            [
                "targa"
            ],
            "Targa",
            "West Marrakech"
        ),

        (
            [
                "chrifia",
                "cherifia"
            ],
            "Chrifia",
            "South-West Marrakech"
        ),

        (
            [
                "sidi ghanem"
            ],
            "Sidi Ghanem",
            "North-West Marrakech"
        ),

        (
            [
                "mhamid",
                "m'hamid"
            ],
            "M'Hamid",
            "South-West Marrakech"
        ),

        (
            [
                "medina",
                "médina"
            ],
            "Medina",
            "Central Marrakech"
        )
    ]

    for keywords, location, corridor in locations:

        for keyword in keywords:

            if keyword in lower:

                return {
                    "micro_location":
                        location,

                    "corridor":
                        corridor
                }

    return {
        "micro_location": None,
        "corridor": None
    }


# ---------------------------------------------------------
# KM MARKER
# ---------------------------------------------------------

def extract_km_marker(text):

    patterns = [
        r"\bkm\s*([0-9]{1,2}(?:[.,][0-9]+)?)\b",
        r"\bkilom[eè]tre\s*([0-9]{1,2}(?:[.,][0-9]+)?)\b",
        r"\bkilometer\s*([0-9]{1,2}(?:[.,][0-9]+)?)\b"
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE
        )

        if match:

            value = match.group(1)
            value = value.replace(
                ",",
                "."
            )

            try:
                km = float(value)

                if (
                    0
                    <= km
                    <= 100
                ):
                    return km

            except ValueError:
                pass

    return None


# ---------------------------------------------------------
# COORDINATES
# ---------------------------------------------------------

def valid_coordinates(
    latitude,
    longitude
):

    try:
        latitude = float(
            latitude
        )

        longitude = float(
            longitude
        )

    except (
        TypeError,
        ValueError
    ):
        return None

    if not (
        -90
        <= latitude
        <= 90
    ):
        return None

    if not (
        -180
        <= longitude
        <= 180
    ):
        return None

    return {
        "latitude":
            latitude,

        "longitude":
            longitude
    }


def extract_coordinates_from_json(
    json_data
):

    for item in walk_json(
        json_data
    ):

        latitude = (
            item.get("latitude")
            or
            item.get("lat")
        )

        longitude = (
            item.get("longitude")
            or
            item.get("lng")
            or
            item.get("lon")
        )

        if (
            latitude is not None
            and
            longitude is not None
        ):

            result = valid_coordinates(
                latitude,
                longitude
            )

            if result:
                return result

        geo = item.get(
            "geo"
        )

        if isinstance(
            geo,
            dict
        ):

            result = valid_coordinates(
                geo.get(
                    "latitude"
                ),
                geo.get(
                    "longitude"
                )
            )

            if result:
                return result

    return None


def extract_coordinates_from_meta(
    soup
):

    latitude = (
        get_meta(
            soup,
            prop="place:location:latitude"
        )
        or
        get_meta(
            soup,
            name="latitude"
        )
    )

    longitude = (
        get_meta(
            soup,
            prop="place:location:longitude"
        )
        or
        get_meta(
            soup,
            name="longitude"
        )
    )

    if (
        latitude
        and
        longitude
    ):

        return valid_coordinates(
            latitude,
            longitude
        )

    return None


# ---------------------------------------------------------
# DISTANCE
# ---------------------------------------------------------

def haversine_distance_km(
    lat1,
    lon1,
    lat2,
    lon2
):

    radius = 6371.0088

    phi1 = math.radians(
        lat1
    )

    phi2 = math.radians(
        lat2
    )

    delta_phi = math.radians(
        lat2 - lat1
    )

    delta_lambda = math.radians(
        lon2 - lon1
    )

    a = (
        math.sin(
            delta_phi / 2
        ) ** 2
        +
        math.cos(phi1)
        *
        math.cos(phi2)
        *
        math.sin(
            delta_lambda / 2
        ) ** 2
    )

    c = 2 * math.atan2(
        math.sqrt(a),
        math.sqrt(1 - a)
    )

    return round(
        radius * c,
        1
    )


# Approximate reference point used only as
# a consistent city-centre benchmark.
MARRAKECH_CENTRE = {
    "latitude": 31.6295,
    "longitude": -7.9811
}


def calculate_distance_to_centre(
    city,
    coordinates
):

    if (
        city != "Marrakech"
        or
        not coordinates
    ):
        return None

    return haversine_distance_km(
        coordinates[
            "latitude"
        ],
        coordinates[
            "longitude"
        ],
        MARRAKECH_CENTRE[
            "latitude"
        ],
        MARRAKECH_CENTRE[
            "longitude"
        ]
    )


# ---------------------------------------------------------
# LOCATION CONFIDENCE
# ---------------------------------------------------------

def calculate_location_confidence(
    city,
    micro_location,
    km_marker,
    coordinates
):

    score = 0

    if city:
        score += 25

    if micro_location:
        score += 30

    if km_marker is not None:
        score += 15

    if coordinates:
        score += 30

    return min(
        score,
        100
    )


# ---------------------------------------------------------
# PRICE / M2
# ---------------------------------------------------------

def calculate_price_per_m2(
    price,
    area
):

    if (
        not price
        or
        not area
    ):
        return None

    if area <= 0:
        return None

    return round(
        price / area
    )


# ---------------------------------------------------------
# MAIN SCANNER
# ---------------------------------------------------------

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

        # -----------------------------
        # TITLE
        # -----------------------------

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

        # -----------------------------
        # DESCRIPTION
        # -----------------------------

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

        # -----------------------------
        # PAGE TEXT
        # -----------------------------

        page_text = clean_text(
            soup.get_text(
                " ",
                strip=True
            )
        )

        # -----------------------------
        # STRUCTURED DATA
        # -----------------------------

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

        # -----------------------------
        # PROPERTY DATA
        # -----------------------------

        price = (
            extract_price_from_json(
                json_ld
            )
            or
            extract_price_from_text(
                combined_text
            )
        )

        area = (
            extract_area_from_json(
                json_ld
            )
            or
            extract_area_from_text(
                combined_text
            )
        )

        bedrooms = extract_bedrooms(
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

        price_per_m2 = (
            calculate_price_per_m2(
                price,
                area
            )
        )

        # -----------------------------
        # LOCATION ENGINE
        # -----------------------------

        location_result = {
            "micro_location": None,
            "corridor": None
        }

        if city == "Marrakech":

            location_result = (
                detect_marrakech_location(
                    combined_text
                )
            )

        micro_location = (
            location_result[
                "micro_location"
            ]
        )

        corridor = (
            location_result[
                "corridor"
            ]
        )

        km_marker = (
            extract_km_marker(
                combined_text
            )
        )

        coordinates = (
            extract_coordinates_from_json(
                json_ld
            )
            or
            extract_coordinates_from_meta(
                soup
            )
        )

        distance_to_centre = (
            calculate_distance_to_centre(
                city,
                coordinates
            )
        )

        location_confidence = (
            calculate_location_confidence(
                city,
                micro_location,
                km_marker,
                coordinates
            )
        )

        distance_verified = (
            distance_to_centre
            is not None
        )

        # -----------------------------
        # EXTRACTION CONFIDENCE
        # -----------------------------

        detected = [
            price,
            area,
            property_type,
            city
        ]

        data_confidence = round(
            (
                sum(
                    value is not None
                    for value in detected
                )
                /
                len(detected)
            )
            * 100
        )

        # -----------------------------
        # STATUS
        # -----------------------------

        if (
            price
            and
            area
            and
            micro_location
        ):

            status = (
                "READY FOR LOCAL MARKET ANALYSIS"
            )

        elif (
            price
            and
            area
        ):

            status = (
                "READY FOR MARKET ANALYSIS"
            )

        elif price:

            status = (
                "AREA REQUIRED"
            )

        elif area:

            status = (
                "PRICE NOT DETECTED"
            )

        else:

            status = (
                "REVIEW LISTING"
            )

        # -----------------------------
        # RESPONSE
        # -----------------------------

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

            "property": {

                "city":
                    city,

                "property_type":
                    property_type,

                "price_mad":
                    price,

                "area_m2":
                    area,

                "bedrooms":
                    bedrooms,

                "rooms":
                    rooms,

                "price_per_m2_mad":
                    price_per_m2
            },

            "location": {

                "city":
                    city,

                "micro_location":
                    micro_location,

                "corridor":
                    corridor,

                "km_marker":
                    km_marker,

                "coordinates":
                    coordinates,

                "distance_to_centre_km":
                    distance_to_centre,

                "distance_verified":
                    distance_verified,

                "location_confidence":
                    location_confidence
            },

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
