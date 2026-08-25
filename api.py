import json
import re
import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__, static_folder=".", static_url_path="")
CORS(app)


@app.route("/", methods=["GET"])
def home():
    return app.send_static_file("index.html")


def clean_text(value):
    if value is None:
        return ""

    return re.sub(r"\s+", " ", str(value)).strip()


def digits_to_int(value):
    if value is None:
        return None

    text = clean_text(value)

    match = re.search(r"\d[\d\s.,]*", text)

    if not match:
        return None

    digits = re.sub(r"[^\d]", "", match.group(0))

    if not digits:
        return None

    try:
        return int(digits)
    except ValueError:
        return None


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

            if isinstance(parsed, list):
                results.extend(parsed)

            elif isinstance(parsed, dict):

                if "@graph" in parsed:
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


def extract_price_from_json(data):
    possible_keys = [
        "price",
        "lowPrice",
        "highPrice"
    ]

    for item in walk_json(data):

        for key in possible_keys:

            if key in item:

                value = digits_to_int(
                    item.get(key)
                )

                if value and 10000 <= value <= 1000000000:
                    return value

        offers = item.get("offers")

        if isinstance(offers, dict):

            value = digits_to_int(
                offers.get("price")
            )

            if value and 10000 <= value <= 1000000000:
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

            if value and 10000 <= value <= 1000000000:
                return value

    return None


def extract_area_from_json(data):
    area_keys = [
        "floorSize",
        "area",
        "surface",
        "size"
    ]

    for item in walk_json(data):

        for key in area_keys:

            if key not in item:
                continue

            value = item.get(key)

            if isinstance(value, dict):

                candidate = (
                    value.get("value") or
                    value.get("minValue") or
                    value.get("maxValue")
                )

            else:
                candidate = value

            number = digits_to_int(candidate)

            if number and 10 <= number <= 100000:
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
                value = int(match.group(1))

                if 10 <= value <= 100000:
                    return value

            except ValueError:
                pass

    return None


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
                value = int(match.group(1))

                if 1 <= value <= 30:
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
                value = int(match.group(1))

                if 1 <= value <= 50:
                    return value

            except ValueError:
                pass

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


def calculate_price_per_m2(price, area):
    if not price or not area:
        return None

    if area <= 0:
        return None

    return round(price / area)


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
            "Accept-Language":
                "en-GB,en;q=0.9,fr;q=0.8"
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

        json_ld = get_json_ld(soup)

        combined_text = clean_text(
            " ".join([
                title,
                description,
                page_text
            ])
        )

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

        property_type = detect_property_type(
            combined_text
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

        detected = [
            price,
            area,
            property_type,
            city
        ]

        confidence = round(
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

        if price and area:

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

        return jsonify({
            "success": True,

            "source": {
                "url": url,
                "final_url":
                    response.url,
                "title": title,
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

            "radar": {
                "status":
                    status,
                "data_confidence":
                    confidence
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
