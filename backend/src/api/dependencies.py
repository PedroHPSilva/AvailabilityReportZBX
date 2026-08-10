from __future__ import annotations

import os
import secrets
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Header, HTTPException

from ..domain.availability_calculator import ASSUME_OK_WHEN_NO_PREVIOUS_EVENT
from ..services.availability_service import AvailabilityWindow
from .schemas import AvailabilityRequest, GroupTriggerAvailabilityRequest, HostAvailabilityRequest


MAX_HISTORY_DAYS = 730


# Dependency do FastAPI: se INTERNAL_API_KEY estiver configurada no .env,
# exige o header X-Internal-API-Key em cada chamada. Se a variavel nao
# estiver setada, a checagem fica desativada (uso local/sem essa camada extra).
def verify_internal_api_key(x_internal_api_key: str | None = Header(default=None)) -> None:
    configured_key = os.getenv("INTERNAL_API_KEY")
    if not configured_key:
        return
    if not x_internal_api_key or not secrets.compare_digest(x_internal_api_key, configured_key):
        raise HTTPException(status_code=401, detail="Chave interna invalida.")


# Converte o payload da requisicao (period_start/period_end/timezone) em um
# AvailabilityWindow validado: timezone existe, fim > inicio, e o inicio nao
# e mais antigo que MAX_HISTORY_DAYS (protege o Zabbix de consultas gigantes).
def build_window(request: AvailabilityRequest | HostAvailabilityRequest | GroupTriggerAvailabilityRequest) -> AvailabilityWindow:
    try:
        timezone = ZoneInfo(request.timezone)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(status_code=400, detail="Fuso horario invalido.") from exc

    period_start = _normalize_datetime(request.period_start, timezone)
    period_end = _normalize_datetime(request.period_end, timezone)
    if period_end <= period_start:
        raise HTTPException(status_code=400, detail="O fim do periodo deve ser posterior ao inicio.")
    earliest_start = datetime.combine(
        datetime.now(timezone).date() - timedelta(days=MAX_HISTORY_DAYS),
        time.min,
        tzinfo=timezone,
    )
    if period_start < earliest_start:
        raise HTTPException(status_code=400, detail="O periodo inicial nao pode ser anterior a 730 dias.")

    return AvailabilityWindow(
        period_start=period_start,
        period_end=period_end,
        timezone_name=request.timezone,
        calculated_at=datetime.now(timezone),
        unknown_initial_state_policy=ASSUME_OK_WHEN_NO_PREVIOUS_EVENT,
    )


# Garante que o datetime tenha o fuso horario correto: se vier "naive" (sem
# tzinfo, caso comum vindo do input datetime-local do frontend), assume o
# fuso informado; se ja tiver tzinfo, converte para o fuso informado.
def _normalize_datetime(value: datetime, timezone: ZoneInfo) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone)
    return value.astimezone(timezone)
