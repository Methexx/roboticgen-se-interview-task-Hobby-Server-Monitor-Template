"""Falcon application factory and HTTP safety middleware."""

from __future__ import annotations

import uuid

import falcon

from hsm.authz.policy import Policy, PolicyRegistry
from hsm.config import Settings


class RequestContextMiddleware:
    def __init__(self, registry: PolicyRegistry) -> None:
        self._registry = registry

    def process_request(self, request: falcon.Request, response: falcon.Response) -> None:
        request.context.request_id = str(uuid.uuid4())
        request.context.policy = self._registry.authorize(request)

    def process_response(
        self,
        request: falcon.Request,
        response: falcon.Response,
        resource: object,
        request_succeeded: bool,
    ) -> None:
        response.set_header("X-Request-ID", request.context.request_id)


def _http_error(
    request: falcon.Request,
    response: falcon.Response,
    error: falcon.HTTPError,
    params: dict[str, object],
) -> None:
    _, _, title = error.status.partition(" ")
    response.status = error.status
    response.media = {
        "error": {"code": title.lower().replace(" ", "_"), "message": error.description},
        "request_id": request.context.request_id,
    }


def _unexpected_error(
    request: falcon.Request,
    response: falcon.Response,
    error: Exception,
    params: dict[str, object],
) -> None:
    response.status = falcon.HTTP_500
    response.media = {
        "error": {"code": "internal_error", "message": "Internal server error"},
        "request_id": request.context.request_id,
    }


class HealthResource:
    def on_get(self, request: falcon.Request, response: falcon.Response) -> None:
        response.media = {"ok": True}


def create_app(settings: Settings) -> falcon.App:
    """Create an API that accepts only explicitly declared request methods."""
    registry = PolicyRegistry()
    app = falcon.App(middleware=[RequestContextMiddleware(registry)])
    app.add_error_handler(falcon.HTTPError, _http_error)
    app.add_error_handler(Exception, _unexpected_error)
    registry.add_route(app, "/healthz", HealthResource(), {"GET": Policy.PUBLIC})
    app.req_options.auto_parse_form_urlencoded = False
    # Settings are injected at construction. Future resources receive the
    # dependencies they need explicitly instead of relying on mutable globals.
    del settings
    return app
