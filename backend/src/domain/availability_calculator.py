from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..integrations.zabbix_client import ZabbixEvent


# Estados possiveis de uma trigger dentro da linha do tempo calculada.
OK = "OK"
PROBLEM = "PROBLEM"
UNKNOWN = "UNKNOWN"
ASSUMED_OK = "ASSUMED_OK"
INCONCLUSIVE = "INCONCLUSIVE"
# Politicas de como tratar o "estado inicial" da trigger quando nao ha
# evento anterior ao periodo (ver _resolve_initial_state()).
ASSUME_OK_WHEN_NO_EVENTS = "ASSUME_OK_WHEN_NO_EVENTS"
ASSUME_OK_WHEN_NO_PREVIOUS_EVENT = "ASSUME_OK_WHEN_NO_PREVIOUS_EVENT"


class AvailabilityCalculationError(ValueError):
    """Raised when events cannot be converted into a state timeline."""


# Um trecho continuo de tempo em que a trigger ficou no mesmo estado
# (ex.: "OK das 08:00 as 10:32"). A uniao de todos os intervalos cobre
# o periodo inteiro, do inicio ao fim.
@dataclass(frozen=True)
class TimelineInterval:
    triggerid: str
    interval_start: datetime
    interval_end: datetime
    state: str
    duration_seconds: int
    source_eventid: str


# Resultado final do calculo de disponibilidade de uma trigger em um
# periodo: percentuais, contagem de incidentes e metadados de auditoria.
@dataclass(frozen=True)
class AvailabilityResult:
    triggerid: str
    trigger_name: str
    period_start: datetime
    period_end: datetime
    timezone: str
    initial_state: str
    total_seconds: int
    ok_seconds: int
    problem_seconds: int
    availability_percent: float | None
    problem_percent: float | None
    incident_count: int
    max_problem_seconds: int
    calculation_status: str
    maintenance_considered: str
    calculated_at: datetime
    observations: str


# Funcao principal: recebe os eventos do Zabbix (o evento imediatamente
# anterior ao periodo + todos os eventos dentro do periodo) e devolve o
# resultado agregado (AvailabilityResult) e a linha do tempo detalhada.
# E' funcao pura (sem I/O), o que facilita testar isoladamente.
def calculate_availability(
    triggerid: str,
    trigger_name: str,
    period_start: datetime,
    period_end: datetime,
    timezone_name: str,
    previous_event: ZabbixEvent | None,
    events_in_window: list[ZabbixEvent],
    calculated_at: datetime,
    unknown_initial_state_policy: str = INCONCLUSIVE,
) -> tuple[AvailabilityResult, list[TimelineInterval]]:
    if period_end <= period_start:
        raise AvailabilityCalculationError("PERIOD_END deve ser posterior a PERIOD_START.")

    initial_state, calculation_status, effective_initial_state = _resolve_initial_state(
        previous_event=previous_event,
        events_in_window=events_in_window,
        unknown_initial_state_policy=unknown_initial_state_policy,
    )
    observations = "Manutencao nao considerada."
    if not previous_event:
        observations += " Sem event anterior ao inicio do periodo."
    if initial_state == ASSUMED_OK:
        observations += " Estado inicial OK assumido para aproximacao ao frontend."

    timeline = _build_timeline(
        triggerid=triggerid,
        period_start=period_start,
        period_end=period_end,
        initial_state=effective_initial_state,
        initial_source_eventid=previous_event.eventid if previous_event else "",
        events_in_window=events_in_window,
    )

    # Denominador do percentual de disponibilidade. IMPORTANTE: period_end
    # e' o fim EXATO informado (ex.: 23:59:59, nao 23:59:00) -- um segundo
    # de diferenca aqui already muda a 3a/4a casa decimal do percentual.
    # Ver PERIODO_23_59_59.md na raiz do projeto para o caso real que
    # motivou esse cuidado.
    total_seconds = int((period_end - period_start).total_seconds())
    ok_seconds = sum(interval.duration_seconds for interval in timeline if interval.state == OK)
    problem_seconds = sum(interval.duration_seconds for interval in timeline if interval.state == PROBLEM)
    problem_intervals = [interval for interval in timeline if interval.state == PROBLEM and interval.duration_seconds > 0]

    availability_percent: float | None = None
    problem_percent: float | None = None
    if calculation_status in {"OK", "PARCIAL"}:
        availability_percent = round((ok_seconds / total_seconds) * 100, 4)
        problem_percent = round((problem_seconds / total_seconds) * 100, 4)

    result = AvailabilityResult(
        triggerid=triggerid,
        trigger_name=trigger_name,
        period_start=period_start,
        period_end=period_end,
        timezone=timezone_name,
        initial_state=initial_state,
        total_seconds=total_seconds,
        ok_seconds=ok_seconds,
        problem_seconds=problem_seconds,
        availability_percent=availability_percent,
        problem_percent=problem_percent,
        incident_count=len(problem_intervals),
        max_problem_seconds=max((interval.duration_seconds for interval in problem_intervals), default=0),
        calculation_status=calculation_status,
        maintenance_considered="NO",
        calculated_at=calculated_at,
        observations=observations,
    )
    return result, timeline


# Decide o estado da trigger no exato instante period_start, com base no
# ultimo evento antes do periodo (o caso comum) ou na politica escolhida
# quando nao ha esse evento (ex.: trigger criada durante o periodo).
def _resolve_initial_state(
    previous_event: ZabbixEvent | None,
    events_in_window: list[ZabbixEvent],
    unknown_initial_state_policy: str,
) -> tuple[str, str, str]:
    if previous_event:
        state = _event_state(previous_event)
        return state, "OK", state

    if unknown_initial_state_policy == ASSUME_OK_WHEN_NO_EVENTS:
        if not events_in_window:
            return ASSUMED_OK, "PARCIAL", OK
        return UNKNOWN, "INCONCLUSIVO", UNKNOWN

    if unknown_initial_state_policy == ASSUME_OK_WHEN_NO_PREVIOUS_EVENT:
        return ASSUMED_OK, "PARCIAL", OK

    if unknown_initial_state_policy != INCONCLUSIVE:
        raise AvailabilityCalculationError(
            "UNKNOWN_INITIAL_STATE_POLICY invalida."
        )

    return UNKNOWN, "INCONCLUSIVO", UNKNOWN


# Percorre os eventos em ordem cronologica e vai fechando intervalos
# (estado anterior) sempre que um novo evento muda o estado da trigger,
# ate cobrir todo o periodo [period_start, period_end].
def _build_timeline(
    triggerid: str,
    period_start: datetime,
    period_end: datetime,
    initial_state: str,
    initial_source_eventid: str,
    events_in_window: list[ZabbixEvent],
) -> list[TimelineInterval]:
    cursor = period_start
    current_state = initial_state
    current_source_eventid = initial_source_eventid
    intervals: list[TimelineInterval] = []

    ordered_events = sorted(events_in_window, key=lambda event: (event.clock, int(event.eventid)))
    for event in ordered_events:
        event_time = datetime.fromtimestamp(event.clock, tz=period_start.tzinfo)
        if event_time < period_start or event_time > period_end:
            continue

        if event_time > cursor:
            intervals.append(
                _make_interval(
                    triggerid=triggerid,
                    interval_start=cursor,
                    interval_end=event_time,
                    state=current_state,
                    source_eventid=current_source_eventid,
                )
            )

        cursor = max(cursor, event_time)
        current_state = _event_state(event)
        current_source_eventid = event.eventid

    if cursor < period_end:
        intervals.append(
            _make_interval(
                triggerid=triggerid,
                interval_start=cursor,
                interval_end=period_end,
                state=current_state,
                source_eventid=current_source_eventid,
            )
        )

    return _merge_adjacent_intervals(intervals)


# Monta um TimelineInterval, ja calculando a duracao em segundos.
def _make_interval(
    triggerid: str,
    interval_start: datetime,
    interval_end: datetime,
    state: str,
    source_eventid: str,
) -> TimelineInterval:
    return TimelineInterval(
        triggerid=triggerid,
        interval_start=interval_start,
        interval_end=interval_end,
        state=state,
        duration_seconds=int((interval_end - interval_start).total_seconds()),
        source_eventid=source_eventid,
    )


# Junta intervalos consecutivos com o mesmo estado em um so (evita
# fragmentar a linha do tempo por causa de eventos redundantes).
def _merge_adjacent_intervals(intervals: list[TimelineInterval]) -> list[TimelineInterval]:
    merged: list[TimelineInterval] = []
    for interval in intervals:
        if (
            merged
            and merged[-1].state == interval.state
            and merged[-1].interval_end == interval.interval_start
        ):
            previous = merged[-1]
            merged[-1] = TimelineInterval(
                triggerid=previous.triggerid,
                interval_start=previous.interval_start,
                interval_end=interval.interval_end,
                state=previous.state,
                duration_seconds=previous.duration_seconds + interval.duration_seconds,
                source_eventid=previous.source_eventid,
            )
        else:
            merged.append(interval)
    return merged


# Traduz o campo "value" do evento do Zabbix (0/1) para OK/PROBLEM.
def _event_state(event: ZabbixEvent) -> str:
    if event.value == 0:
        return OK
    if event.value == 1:
        return PROBLEM
    raise AvailabilityCalculationError(f"Valor de event nao suportado: {event.value}")
