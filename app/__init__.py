from flask import Flask
from .blueprints.legacy import bp as legacy_bp

def create_app():
    app = Flask(__name__, template_folder='templates', static_folder='static')
    app.register_blueprint(legacy_bp)
    return app
