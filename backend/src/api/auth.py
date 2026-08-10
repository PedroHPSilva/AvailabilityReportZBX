from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass

from fastapi import Request, Response


SESSION_COOKIE_NAME = "zabbix_availability_session"
DEFAULT_SESSION_TTL_SECONDS = 8 * 60 * 60


# Uma sessao web local guarda o auth_token que o Zabbix devolveu no login,
# para nao pedir usuario/senha de novo a cada chamada.
@dataclass(frozen=True)
class AuthSession:
    username: str
    auth_token: str
    expires_at: float


# Guarda as sessoes em memoria do proprio processo (nao sobrevive a
# restart nem escala com multiplos workers uvicorn -- ver ANALISE_E_MELHORIAS.md).
class InMemorySessionStore:
    def __init__(self, ttl_seconds: int | None = None) -> None:
        self.ttl_seconds = ttl_seconds or _session_ttl_seconds()
        self._sessions: dict[str, AuthSession] = {}

    # Cria uma sessao nova apos login bem-sucedido no Zabbix e devolve o
    # id que vai para o cookie do navegador.
    def create(self, username: str, auth_token: str) -> str:
        self._cleanup_expired()
        session_id = secrets.token_urlsafe(32)
        self._sessions[session_id] = AuthSession(
            username=username,
            auth_token=auth_token,
            expires_at=time.time() + self.ttl_seconds,
        )
        return session_id

    # Recupera a sessao a partir do cookie da requisicao (None se nao
    # existir ou tiver expirado).
    def find(self, request: Request) -> AuthSession | None:
        self._cleanup_expired()
        session_id = request.cookies.get(SESSION_COOKIE_NAME)
        return self._sessions.get(session_id) if session_id else None

    # Usado no logout: remove a sessao correspondente ao cookie atual.
    def delete_from_request(self, request: Request) -> None:
        session_id = request.cookies.get(SESSION_COOKIE_NAME)
        if session_id:
            self._sessions.pop(session_id, None)

    # Remove sessoes vencidas a cada operacao (nao ha job em background
    # separado; simples o suficiente para o volume de uso esperado).
    def _cleanup_expired(self) -> None:
        now = time.time()
        expired_ids = [session_id for session_id, session in self._sessions.items() if session.expires_at <= now]
        for session_id in expired_ids:
            self._sessions.pop(session_id, None)


# Grava o cookie de sessao (HttpOnly para nao ser lido via JS).
def set_session_cookie(response: Response, session_id: str, ttl_seconds: int) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_id,
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        max_age=ttl_seconds,
        path="/",
    )


# Apaga o cookie de sessao no logout.
def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        path="/",
    )


# Le SESSION_TTL_SECONDS do .env, com fallback seguro se vier invalido.
def _session_ttl_seconds() -> int:
    raw_value = os.getenv("SESSION_TTL_SECONDS", str(DEFAULT_SESSION_TTL_SECONDS))
    try:
        value = int(raw_value)
    except ValueError:
        return DEFAULT_SESSION_TTL_SECONDS
    return value if value > 0 else DEFAULT_SESSION_TTL_SECONDS


# SESSION_COOKIE_SECURE=true exige HTTPS para o cookie ser enviado; use
# assim que o servidor tiver certificado configurado.
def _cookie_secure() -> bool:
    return os.getenv("SESSION_COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes", "on"}
