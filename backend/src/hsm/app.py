"""Falcon application factory and HTTP safety middleware."""

from __future__ import annotations

import uuid

import falcon

from hsm.auth.sessions import SessionService
from hsm.api.users import AssignmentResource, UserResource, UsersResource
from hsm.authz.policy import Policy, PolicyRegistry
from hsm.config import Settings


class RequestContextMiddleware:
    def __init__(self, registry: PolicyRegistry, sessions: SessionService) -> None:
        self._registry = registry
        self._sessions = sessions

    def process_request(self, request: falcon.Request, response: falcon.Response) -> None:
        request.context.request_id = str(uuid.uuid4())
        request.context.policy = self._registry.authorize(request)
        request.context.user = self._sessions.resolve(request.get_cookie_values("hsm_session")[0]
                                                      if request.get_cookie_values("hsm_session") else None)
        if request.context.policy != Policy.PUBLIC and request.context.user is None:
            raise falcon.HTTPUnauthorized(description="An active session is required")
        if request.context.policy == Policy.ADMIN and request.context.user.role != "admin":
            raise falcon.HTTPForbidden(description="Administrator access is required")

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
    sessions = SessionService(
        settings.database_path,
        idle_seconds=settings.session_idle_seconds,
        absolute_seconds=settings.session_absolute_seconds,
    )
    app = falcon.App(middleware=[RequestContextMiddleware(registry, sessions)])
    app.add_error_handler(falcon.HTTPError, _http_error)
    app.add_error_handler(Exception, _unexpected_error)
    registry.add_route(app, "/healthz", HealthResource(), {"GET": Policy.PUBLIC})
    registry.add_route(app, "/api/users", UsersResource(settings.database_path), {
        "GET": Policy.ADMIN, "POST": Policy.ADMIN,
    })
    registry.add_route(app, "/api/users/{user_id}", UserResource(settings.database_path), {
        "PATCH": Policy.ADMIN, "DELETE": Policy.ADMIN,
    })
    registry.add_route(app, "/api/users/{user_id}/containers/{container_id}", AssignmentResource(settings.database_path), {
        "PUT": Policy.ADMIN, "DELETE": Policy.ADMIN,
    })
    app.req_options.auto_parse_form_urlencoded = False
    return app
