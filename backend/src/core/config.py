from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


# Raiz do projeto (pasta "backend"), usada para localizar o .env e a pasta
# de logs (ver core/logging_config.py) de forma independente de onde o
# processo foi iniciado.
def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


# Carrega backend/.env para as variaveis de ambiente do processo. Chamado
# uma vez na criacao do app (api/app.py) e tambem pela CLI (main.py).
def load_project_env() -> None:
    load_dotenv(project_root() / ".env", encoding="utf-8-sig")


# Le CORS_ORIGINS do .env (lista separada por virgula) com um default
# sensato para desenvolvimento local (frontend rodando em :3000).
def get_cors_origins() -> list[str]:
    raw_origins = os.getenv("CORS_ORIGINS", "http://127.0.0.1:3000,http://localhost:3000")
    return [origin.strip() for origin in raw_origins.split(",") if origin.strip()]
