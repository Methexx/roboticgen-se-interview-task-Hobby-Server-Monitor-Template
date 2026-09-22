"""Gunicorn entrypoint; configuration is supplied by the service environment."""
from hsm.app import create_app
from hsm.config import load_settings

app = create_app(load_settings())
