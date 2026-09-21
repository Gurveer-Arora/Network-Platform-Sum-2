"""Entry point. Builds the app and wires the blueprints together."""

from flask import Flask

from api import api_bp
from website import website_bp


def create_app():
    app = Flask(__name__)

    # Config goes here later (secret key, DB URI, etc.)
    app.config["DEBUG"] = True

    app.register_blueprint(api_bp)
    app.register_blueprint(website_bp)

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)