from __future__ import annotations

import logging
import os
import time
import uuid
from collections.abc import Callable

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ..core.config import get_cors_origins, load_project_env
from ..core.logging_config import configure_logging, get_logger, set_request_id
from ..integrations.zabbix_client import ZabbixClient, ZabbixClientError, ZabbixHttpError, ZabbixJsonRpcError
from ..services.availability_service import AvailabilityService
from .auth import InMemorySessionStore, clear_session_cookie, set_session_cookie
from .routes import build_router
from .schemas import AuthSessionResponse, LoginRequest, SsoLoginRequest

logger = get_logger("zabbix_automation.api")


def create_app(
    service_factory: Callable[[], AvailabilityService] | None = None,
    zabbix_client_factory: Callable[[str], ZabbixClient] | None = None,
) -> FastAPI:
    load_project_env()
    configure_logging()
    app = FastAPI(title="Zabbix Availability API", version="0.1.0")
    app.state.service_factory = service_factory
    app.state.zabbix_client_factory = zabbix_client_factory or ZabbixClient
    app.state.sessions = InMemorySessionStore()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_cors_origins(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Internal-API-Key", "X-Request-Id"],
        expose_headers=["X-Request-Id"],
    )

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        # Reaproveita o ID enviado pelo frontend (frontend/src/logger.ts) para
        # que o mesmo identificador apareça no console do navegador e nas
        # linhas de log do backend (logs/app.log e logs/app-error.log).
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        set_request_id(request_id)
        request.state.request_id = request_id
        started_at = time.monotonic()
        response = await call_next(request)
        duration_ms = round((time.monotonic() - started_at) * 1000, 1)
        response.headers["X-Request-Id"] = request_id
        logger.info(
            "%s %s -> %s (%sms)",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response

    def require_authentication(request: Request) -> None:
        if app.state.service_factory is not None:
            return
        if app.state.sessions.find(request) is None:
            raise HTTPException(status_code=401, detail="Sessao nao autenticada.")

    def get_service(request: Request) -> AvailabilityService:
        if app.state.service_factory is not None:
            return app.state.service_factory()
        session = app.state.sessions.find(request)
        if session is None:
            raise HTTPException(status_code=401, detail="Sessao nao autenticada.")
        client = app.state.zabbix_client_factory(_zabbix_url())
        client.use_auth_token(session.auth_token)
        return AvailabilityService(client)

    def authenticate(payload: LoginRequest, response: Response) -> AuthSessionResponse:
        client = app.state.zabbix_client_factory(_zabbix_url())
        try:
            client.login(payload.username, payload.password)
        except ZabbixJsonRpcError as exc:
            logger.warning("Login rejeitado pelo Zabbix para usuario=%s: %s", payload.username, exc)
            raise HTTPException(status_code=401, detail="Usuario ou senha invalidos.") from exc
        except ZabbixClientError as exc:
            logger.error("Falha ao contatar o Zabbix durante login de usuario=%s: %s", payload.username, exc, exc_info=True)
            raise HTTPException(status_code=502, detail="Nao foi possivel acessar o Zabbix.") from exc
        except Exception as exc:
            logger.error("Erro inesperado durante login de usuario=%s: %s", payload.username, exc, exc_info=True)
            raise HTTPException(
                status_code=502,
                detail="Nao foi possivel acessar o Zabbix.",
            ) from exc
        if not client.auth_token:
            logger.warning("Login sem auth_token retornado pelo Zabbix para usuario=%s", payload.username)
            raise HTTPException(status_code=401, detail="Usuario ou senha invalidos.")
        session_id = app.state.sessions.create(payload.username, client.auth_token)
        set_session_cookie(response, session_id, app.state.sessions.ttl_seconds)
        logger.info("Login bem-sucedido para usuario=%s", payload.username)
        return AuthSessionResponse(authenticated=True, username=payload.username)

    def sso_authenticate(payload: SsoLoginRequest, response: Response) -> AuthSessionResponse:
        # Login sem usuario/senha: reaproveita o sessionid de uma sessao ja
        # autenticada no proprio Zabbix (enviado pelo modulo em
        # zabbix-module/, quando a aplicacao e' aberta via iframe dentro do
        # Zabbix). O sessionid do frontend do Zabbix E' um token valido da
        # API (ver CWebUser::$data['sessionid']), entao basta validar com
        # user.checkAuthentication -- nao ha senha nenhuma envolvida aqui.
        client = app.state.zabbix_client_factory(_zabbix_url())
        try:
            user_data = client.check_authentication(payload.session_id)
        except ZabbixJsonRpcError as exc:
            logger.warning("SSO rejeitado pelo Zabbix (sessionid invalido/expirado): %s", exc)
            raise HTTPException(status_code=401, detail="Sessao do Zabbix invalida ou expirada.") from exc
        except ZabbixClientError as exc:
            logger.error("Falha ao contatar o Zabbix durante SSO: %s", exc, exc_info=True)
            raise HTTPException(status_code=502, detail="Nao foi possivel acessar o Zabbix.") from exc

        username = user_data.get("username") or user_data.get("alias") or "zabbix"
        client.use_auth_token(payload.session_id)
        session_id = app.state.sessions.create(username, payload.session_id)
        set_session_cookie(response, session_id, app.state.sessions.ttl_seconds)
        logger.info("Login via SSO (modulo Zabbix) bem-sucedido para usuario=%s", username)
        return AuthSessionResponse(authenticated=True, username=username)

    def session_status(request: Request) -> AuthSessionResponse:
        session = app.state.sessions.find(request)
        return AuthSessionResponse(
            authenticated=session is not None,
            username=session.username if session else None,
        )

    def terminate_session(request: Request, response: Response) -> AuthSessionResponse:
        app.state.sessions.delete_from_request(request)
        clear_session_cookie(response)
        return AuthSessionResponse(authenticated=False)

    @app.exception_handler(ZabbixHttpError)
    def zabbix_http_error_handler(request: Request, exc: ZabbixHttpError) -> JSONResponse:
        logger.error("Falha de conexao com o Zabbix em %s: %s", request.url.path, exc, exc_info=True)
        return _error_response(
            502,
            "ZABBIX_CONNECTION_ERROR",
            "Nao foi possivel conectar ao Zabbix. Verifique se a ZABBIX_URL esta correta e se o servidor Zabbix esta acessivel.",
            request=request,
        )

    @app.exception_handler(ZabbixClientError)
    def zabbix_error_handler(request: Request, exc: ZabbixClientError) -> JSONResponse:
        logger.error("Erro ao consultar a API do Zabbix em %s: %s", request.url.path, exc, exc_info=True)
        return _error_response(
            502,
            "ZABBIX_API_ERROR",
            "O Zabbix retornou um erro ao processar a solicitacao.",
            details=str(exc),
            request=request,
        )

    @app.exception_handler(HTTPException)
    def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
        # 401 de sessao expirada e comum e esperado (nao e um bug), fica em INFO.
        # Demais 4xx ficam em WARNING; erros 5xx nunca deveriam vir por aqui
        # (tem handlers dedicados acima), mas caso venham, tambem viram WARNING.
        log_level = logging.INFO if exc.status_code == 401 else logging.WARNING
        logger.log(log_level, "HTTPException %s em %s: %s", exc.status_code, request.url.path, exc.detail)
        return _error_response(exc.status_code, "HTTP_ERROR", str(exc.detail), request=request)

    @app.exception_handler(ValueError)
    def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
        # ValueError aqui vem de validacoes de negocio (ex.: AvailabilityService),
        # nao de bugs. A mensagem e segura para mostrar ao usuario.
        logger.warning("Erro de validacao de negocio em %s: %s", request.url.path, exc)
        return _error_response(400, "BUSINESS_VALIDATION_ERROR", str(exc), request=request)

    @app.exception_handler(RequestValidationError)
    def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        logger.warning("Payload invalido em %s: %s", request.url.path, exc.errors())
        return _error_response(422, "VALIDATION_ERROR", "Payload invalido.", details=exc.errors(), request=request)

    @app.exception_handler(Exception)
    def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error("Erro inesperado em %s: %s", request.url.path, exc, exc_info=True)
        return _error_response(
            500,
            "UNEXPECTED_ERROR",
            "Erro interno ao processar a solicitacao.",
            request=request,
        )

    app.include_router(
        build_router(
            get_service=get_service,
            authenticate=authenticate,
            sso_authenticate=sso_authenticate,
            session_status=session_status,
            terminate_session=terminate_session,
            require_authentication=require_authentication,
        )
    )
    return app


app = create_app()


def _zabbix_url() -> str:
    zabbix_url = os.getenv("ZABBIX_URL", "").strip()
    if not zabbix_url:
        raise HTTPException(status_code=500, detail="Configuracao do Zabbix ausente no backend.")
    return zabbix_url


def _error_response(
    status_code: int,
    error: str,
    message: str,
    details: object | None = None,
    request: Request | None = None,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None) if request is not None else None
    return JSONResponse(
        status_code=status_code,
        content={
            "error": error,
            "message": message,
            "details": details,
            "request_id": request_id,
        },
    )
