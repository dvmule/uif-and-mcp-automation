"""FastAPI app that serves the registered openapi.yaml exactly -- same paths,
same status codes, same problem+json errors. create_app() takes its store so
tests can inject a fresh one."""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .domain import DeveloperPage, RegisterDeveloperRequest
from .middleware import observability, require_bearer
from .problem import ProblemException, problem, problem_response
from .store import DeveloperStore, EmailTakenError


def create_app(store: Optional[DeveloperStore] = None) -> FastAPI:
    store = store or DeveloperStore()
    # FastAPI can generate its own OpenAPI, but UIF is API First: the hand-written
    # openapi.yaml is the source of truth and the contract test validates against it.
    app = FastAPI(title="Product Developer Registration API", version="1.0.0")
    app.middleware("http")(observability)

    @app.exception_handler(ProblemException)
    async def _problem_handler(_request: Request, exc: ProblemException) -> JSONResponse:
        return problem_response(exc.body)

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {"msg": "invalid request"}
        field = ".".join(str(p) for p in first.get("loc", [])[1:]) or "body"
        detail = f"{field}: {first.get('msg', 'invalid')}"
        return problem_response(
            problem("validation-error", "Validation failed", 400, detail, request.url.path)
        )

    @app.exception_handler(Exception)
    async def _unhandled_handler(request: Request, _exc: Exception) -> JSONResponse:
        return problem_response(
            problem("internal", "Internal server error", 500, "An unexpected error occurred", request.url.path)
        )

    @app.post("/api/v1/developers", status_code=201, dependencies=[Depends(require_bearer)])
    async def register_developer(body: RegisterDeveloperRequest, request: Request, response: Response):
        try:
            developer = store.create(body.email, body.displayName)
        except EmailTakenError as err:
            raise ProblemException(
                problem("email-taken", "Email already registered", 409,
                        f"A developer with email {err} already exists", request.url.path)
            )
        response.headers["Location"] = f"/api/v1/developers/{developer.id}"
        return developer

    @app.get("/api/v1/developers", dependencies=[Depends(require_bearer)])
    async def list_developers(
        pageSize: int = Query(default=50, ge=1, le=100),
        cursor: Optional[str] = Query(default=None),
    ) -> DeveloperPage:
        items, next_cursor = store.list(pageSize, cursor)
        return DeveloperPage(items=items, nextCursor=next_cursor)

    @app.get("/api/v1/developers/{developerId}", dependencies=[Depends(require_bearer)])
    async def get_developer(developerId: str, request: Request):
        developer = store.get(developerId)
        if developer is None:
            raise ProblemException(
                problem("not-found", "Developer not found", 404,
                        f"No developer with id {developerId}", request.url.path)
            )
        return developer

    return app


app = create_app()
