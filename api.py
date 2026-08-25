import re
import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)


@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "ok",
        "service": "Property Radar API"
    })


def clean_text(text):
    return re.sub(r"\s+", " ", text or "").strip()


def extract_number(value):
    if not value:
        return None

    value = value.replace("\xa0", " ")
    numbers = re.findall(r"\d[\d\s.,]*", value)

    if not numbers:
        return None

    number = numbers[0]
    number = re.sub(r"[^\d]", "", number)

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


def calculate_price_per_m2(price, area):
    if not price or not area:
        return None

    try:
        return round(price / area)
    except (TypeError, ZeroDivisionError):
        return None


@app.route("/api/scan", methods=["POST"])
def scan_property():

    data = request.get_json(silent=True) or {}
    url = data.get("url", "").strip()

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
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/138.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-GB,en;q=0.9,fr;q=0.8"
        }

        response = requests.get(
            url,
            headers=headers,
            timeout=15
        )

        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        title = ""

        if soup.title:
            title = clean_text(
                soup.title.get_text(" ", strip=True)
            )

        description = ""

        description_tag = soup.find(
            "meta",
            attrs={"name": "description"}
        )

        if description_tag:
            description = clean_text(
                description_tag.get("content", "")
            )

        if not description:
            og_description = soup.find(
                "meta",
                attrs={"property": "og:description"}
            )

            if og_description:
                description = clean_text(
                    og_description.get("content", "")
                )

        page_text = clean_text(
            soup.get_text(" ", strip=True)
        )

        analysis_text = " ".join([
            title,
            description,
            page_text[:20000]
        ])

        price = extract_price(analysis_text)
        area = extract_area(analysis_text)
        bedrooms = extract_bedrooms(analysis_text)
        property_type = detect_property_type(analysis_text)
        city = detect_city(analysis_text)

        price_per_m2 = calculate_price_per_m2(
            price,
            area
        )

        detected_fields = 0

        for value in [
            price,
            area,
            bedrooms,
            property_type,
            city
        ]:
            if value is not None:
                detected_fields += 1

        confidence = round(
            (detected_fields / 5) * 100
        )

        if price and area:
            radar_status = "Ready for market analysis"
        elif price:
            radar_status = "Area required for price analysis"
        elif area:
            radar_status = "Price not detected"
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
                "property_type": property_type,
                "price_mad": price,
                "area_m2": area,
                "bedrooms": bedrooms,
                "price_per_m2_mad": price_per_m2
            },

            "radar": {
                "status": radar_status,
                "data_confidence": confidence
            },

            "content": page_text[:12000]
        })

    except requests.RequestException as error:

        return jsonify({
            "success": False,
            "error": "Property listing could not be retrieved.",
            "details": str(error)
        }), 502

    except Exception as error:

        return jsonify({
            "success": False,
            "error": "Property scan failed.",
            "details": str(error)
        }), 500


if __name__ == "__main__":
    app.run(debug=True)
