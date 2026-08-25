import os
import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__, static_folder=".", static_url_path="")
CORS(app)


@app.route("/", methods=["GET"])
def home():
    return app.send_static_file("index.html")


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
            )
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
            title = soup.title.get_text(" ", strip=True)

        description = ""

        description_tag = soup.find(
            "meta",
            attrs={"name": "description"}
        )

        if description_tag:
            description = description_tag.get("content", "")

        page_text = soup.get_text(
            " ",
            strip=True
        )

        page_text = page_text[:12000]

        return jsonify({
            "success": True,
            "source_url": url,
            "title": title,
            "description": description,
            "content": page_text
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
