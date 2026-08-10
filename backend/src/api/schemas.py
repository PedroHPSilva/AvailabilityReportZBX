from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


# Modelos Pydantic de entrada/saida da API HTTP. Ficam separados dos
# modelos internos (services/availability_service.py) de proposito: o
# formato exposto ao frontend pode mudar sem afetar a logica interna, e
# vice-versa.


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


# Login via SSO com o Zabbix: o modulo (zabbix-module/) envia o sessionid da
# sessao ja autenticada no proprio Zabbix, em vez de usuario/senha.
class SsoLoginRequest(BaseModel):
    session_id: str = Field(min_length=1)


class AuthSessionResponse(BaseModel):
    authenticated: bool
    username: str | None = None


class HostResponse(BaseModel):
    hostid: str
    name: str
    host: str


class HostGroupResponse(BaseModel):
    groupid: str
    name: str


class TriggerResponse(BaseModel):
    triggerid: str
    description: str
    status: str
    value: str
    hosts: list[HostResponse]
    grouping_source: str
    grouping_source_id: str
    grouping_label: str


class HostsResponse(BaseModel):
    count: int
    hosts: list[HostResponse]


class HostGroupsResponse(BaseModel):
    count: int
    groups: list[HostGroupResponse]


class TriggersResponse(BaseModel):
    hostid: str
    count: int
    triggers: list[TriggerResponse]


class TriggerGroupResponse(BaseModel):
    key: str
    description: str
    grouping_source: str
    grouping_source_id: str
    grouping_label: str
    trigger_count: int
    host_count: int
    hosts: list[HostResponse]


class TriggerGroupsResponse(BaseModel):
    count: int
    triggers: list[TriggerGroupResponse]


class AvailabilityRequest(BaseModel):
    triggerid: str = Field(min_length=1)
    # Aceitam datetime ISO 8601 com segundos (ex.: "...T23:59:59"). O
    # frontend por padrao usa 00:00:00 -> 23:59:59, igual a convencao do
    # Zabbix (ver PERIODO_23_59_59.md na raiz do projeto).
    period_start: datetime
    period_end: datetime
    timezone: str = Field(min_length=1)


class HostAvailabilityRequest(BaseModel):
    hostid: str = Field(min_length=1)
    period_start: datetime
    period_end: datetime
    timezone: str = Field(min_length=1)
    search: str | None = None
    limit: int = Field(default=10, ge=1, le=100)


class GroupTriggerAvailabilityRequest(BaseModel):
    trigger_keys: list[str] = Field(min_length=1)
    period_start: datetime
    period_end: datetime
    timezone: str = Field(min_length=1)
    groupids: list[str] = Field(default_factory=list)
    hostids: list[str] = Field(default_factory=list)
    host_limit: int | None = Field(default=None, ge=1)
    trigger_limit: int | None = Field(default=None, ge=1)


class AvailabilityResultResponse(BaseModel):
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
    hosts: list[HostResponse]
    grouping_source: str
    grouping_source_id: str
    grouping_label: str


class TimelineIntervalResponse(BaseModel):
    triggerid: str
    interval_start: datetime
    interval_end: datetime
    state: str
    duration_seconds: int
    source_eventid: str


class CalculationAuditResponse(BaseModel):
    previous_event_found: bool
    previous_eventid: str
    events_in_window_count: int
    timeline_intervals_count: int
    initial_state_source: str
    maintenance_considered: bool


class TimelineResponse(BaseModel):
    result: AvailabilityResultResponse
    audit: CalculationAuditResponse
    intervals: list[TimelineIntervalResponse]


class HostAvailabilityResponse(BaseModel):
    hostid: str
    count: int
    results: list[AvailabilityResultResponse]


class GroupTriggerAvailabilityResponse(BaseModel):
    key: str
    description: str
    grouping_source: str
    grouping_source_id: str
    grouping_label: str
    host_count: int
    calculated_count: int
    ok_count: int
    partial_count: int
    inconclusive_count: int
    average_availability_percent: float | None
    worst_availability_percent: float | None
    best_availability_percent: float | None
    total_incident_count: int
    max_problem_seconds: int
    results: list[AvailabilityResultResponse]


class FiltersResponse(BaseModel):
    timezone_required: bool
    default_list_limit: int
    max_list_limit: int
    default_host_calculation_limit: int
    max_host_calculation_limit: int
    maintenance_considered: bool


class ErrorResponse(BaseModel):
    error: str
    message: str
    details: object | None = None


class HealthResponse(BaseModel):
    status: str
