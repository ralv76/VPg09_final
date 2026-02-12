"""Register all blueprints."""
from backend.routes.main import main_bp
from backend.routes.api import api_bp


def register_routes(app):
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp, url_prefix="/api")
