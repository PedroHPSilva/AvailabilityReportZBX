from __future__ import annotations

import logging
import logging.handlers
import os
from contextvars import ContextVar
from pathlib import Path

from .config import project_root

# ID da requisição HTTP atual (preenchido pelo middleware em app.py).
# Usado pelo RequestIdFilter para que toda linha de log fique correlacionada
# com a resposta que o frontend recebeu (e com o "X-Request-Id" mostrado ao usuário).
_request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


def set_request_id(request_id: str) -> None:
    _request_id_ctx.set(request_id)


def get_request_id() -> str:
    return _request_id_ctx.get()


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_ctx.get()
        return True


LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | rid=%(request_id)s | %(message)s"


def _log_dir() -> Path:
    configured = os.getenv("LOG_DIR")
    if configured:
        return Path(configured)
    # backend/.. -> raiz do projeto, mesma pasta "logs" usada pelos scripts
    # PowerShell e pela unit systemd (deploy/systemd/zabbix-automation-backend.service).
    return project_root().parent / "logs"


def _rotating_file_handler(path: Path, level: int, formatter: logging.Formatter, request_id_filter: RequestIdFilter) -> logging.Handler | None:
    # Se o processo nao tiver permissao de escrita no arquivo (ex.: arquivo
    # criado anteriormente por outro usuario, como um teste manual rodado
    # como root), NAO derrubamos a aplicacao inteira por causa do logging.
    # Preferimos rodar so com log no console (stdout, capturado pelo
    # journalctl/systemd) a a aplicacao nem subir. Ver deploy/README.md,
    # secao "Problemas de permissao em logs/".
    try:
        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
    except OSError:
        return None
    handler.setFormatter(formatter)
    handler.addFilter(request_id_filter)
    handler.setLevel(level)
    return handler


def configure_logging() -> None:
    log_dir = _log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)

    level_name = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)

    formatter = logging.Formatter(LOG_FORMAT)
    request_id_filter = RequestIdFilter()

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(request_id_filter)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    # Evita handlers duplicados se create_app() for chamado mais de uma vez
    # (acontece nos testes, que instanciam o app repetidamente).
    root_logger.handlers.clear()
    root_logger.addHandler(console_handler)

    # Log geral da aplicação (INFO+): logins, chamadas ao Zabbix, cálculos.
    app_handler = _rotating_file_handler(log_dir / "app.log", logging.INFO, formatter, request_id_filter)
    # Log só de erros (ERROR+), com stack trace completo. É o primeiro lugar
    # a olhar quando algo falhou na aplicação ou na integração com o Zabbix.
    error_handler = _rotating_file_handler(log_dir / "app-error.log", logging.ERROR, formatter, request_id_filter)

    if app_handler is not None:
        root_logger.addHandler(app_handler)
    if error_handler is not None:
        root_logger.addHandler(error_handler)
    if app_handler is None or error_handler is None:
        root_logger.warning(
            "Sem permissao de escrita em %s -- logging de arquivo desabilitado, "
            "usando so console/journalctl. Corrija com: "
            "sudo chown -R zabbixauto:zabbixauto %s",
            log_dir,
            log_dir,
        )

    # O logger de acesso do uvicorn já imprime "GET /api/x 200 OK"; mantemos
    # esses logs, mas sem duplicar em cima do nosso próprio formato.
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
