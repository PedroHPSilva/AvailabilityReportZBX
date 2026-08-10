from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Query, Request, Response

from ..services.availability_service import (
    AvailabilityService,
    GroupTriggerAvailability,
    HostGroupSummary,
    HostSummary,
    TriggerAvailability,
    TriggerGroupSummary,
    TriggerSummary,
)
from .dependencies import build_window, verify_internal_api_key
from .schemas import (
    AvailabilityRequest,
    AvailabilityResultResponse,
    AuthSessionResponse,
    CalculationAuditResponse,
    FiltersResponse,
    GroupTriggerAvailabilityRequest,
    GroupTriggerAvailabilityResponse,
    HealthResponse,
    HostGroupResponse,
    HostGroupsResponse,
    HostAvailabilityRequest,
    HostAvailabilityResponse,
    HostResponse,
    HostsResponse,
    LoginRequest,
    SsoLoginRequest,
    TimelineIntervalResponse,
    TimelineResponse,
    TriggerGroupResponse,
    TriggerGroupsResponse,
    TriggerResponse,
    TriggersResponse,
)


# Monta todas as rotas HTTP da API. Recebe como parametros as funcoes de
# autenticacao definidas em api/app.py (injecao simples, sem framework
# de DI) para nao criar dependencia circular entre routes.py e app.py.
# `protected` e a lista de dependencias aplicada a toda rota que exige
# sessao ativa (cookie) + a chave interna opcional (X-Internal-API-Key).
def build_router(get_service, authenticate, sso_authenticate, session_status, terminate_session, require_authentication) -> APIRouter:
    router = APIRouter()
    protected = [Depends(require_authentication), Depends(verify_internal_api_key)]

    # Endpoint de healthcheck (sem autenticacao) -- usado por monitoramento
    # externo e como teste rapido de que o backend esta de pe.
    @router.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok")

    # --- Autenticacao: login/sessao/logout (ver api/auth.py) ---
    @router.post("/api/auth/login", response_model=AuthSessionResponse)
    def login(payload: LoginRequest, response: Response) -> AuthSessionResponse:
        return authenticate(payload, response)

    # Login sem usuario/senha, usado quando a aplicacao e' aberta dentro do
    # Zabbix via o modulo (zabbix-module/) -- ver Module.php e
    # views/availability.view.php, que passam o sessionid do usuario ja
    # logado no Zabbix.
    @router.post("/api/auth/sso", response_model=AuthSessionResponse)
    def sso_login(payload: SsoLoginRequest, response: Response) -> AuthSessionResponse:
        return sso_authenticate(payload, response)

    @router.get("/api/auth/session", response_model=AuthSessionResponse)
    def session(request: Request) -> AuthSessionResponse:
        return session_status(request)

    @router.post("/api/auth/logout", response_model=AuthSessionResponse)
    def logout(request: Request, response: Response) -> AuthSessionResponse:
        return terminate_session(request, response)

    # Metadados estaticos para o frontend configurar limites/comportamento
    # da UI de filtros sem precisar hardcodar esses valores no front.
    @router.get("/api/filters", response_model=FiltersResponse, dependencies=protected)
    def filters() -> FiltersResponse:
        return FiltersResponse(
            timezone_required=True,
            default_list_limit=0,
            max_list_limit=0,
            default_host_calculation_limit=10,
            max_host_calculation_limit=100,
            maintenance_considered=False,
        )

    # --- Listagens usadas para popular os filtros (grupos/hosts/triggers) ---
    @router.get("/api/hosts", response_model=HostsResponse, dependencies=protected)
    def list_hosts(
        search: str | None = None,
        groupids: list[str] = Query(default=[]),
        limit: int | None = Query(default=None, ge=1),
        service: AvailabilityService = Depends(get_service),
    ) -> HostsResponse:
        hosts = (
            service.list_hosts_by_groups(groupids, search=search, limit=limit)
            if groupids
            else service.list_hosts(search=search, limit=limit)
        )
        return HostsResponse(count=len(hosts), hosts=[_host_response(host) for host in hosts])

    @router.get("/api/hostgroups", response_model=HostGroupsResponse, dependencies=protected)
    def list_hostgroups(
        search: str | None = None,
        limit: int | None = Query(default=None, ge=1),
        service: AvailabilityService = Depends(get_service),
    ) -> HostGroupsResponse:
        groups = service.list_hostgroups(search=search, limit=limit)
        return HostGroupsResponse(count=len(groups), groups=[_hostgroup_response(group) for group in groups])

    @router.get("/api/triggers", response_model=TriggerGroupsResponse, dependencies=protected)
    def list_trigger_groups(
        groupids: list[str] = Query(default=[]),
        hostids: list[str] = Query(default=[]),
        search: str | None = None,
        host_limit: int | None = Query(default=None, ge=1),
        trigger_limit: int | None = Query(default=None, ge=1),
        service: AvailabilityService = Depends(get_service),
    ) -> TriggerGroupsResponse:
        triggers = service.list_trigger_groups(
            group_ids=groupids,
            host_ids=hostids,
            search=search,
            host_limit=host_limit,
            trigger_limit=trigger_limit,
        )
        return TriggerGroupsResponse(count=len(triggers), triggers=[_trigger_group_response(trigger) for trigger in triggers])

    @router.get(
        "/api/hosts/{hostid}/triggers",
        response_model=TriggersResponse,
        dependencies=protected,
    )
    def list_triggers(
        hostid: str,
        search: str | None = None,
        limit: int | None = Query(default=None, ge=1),
        service: AvailabilityService = Depends(get_service),
    ) -> TriggersResponse:
        triggers = service.list_triggers_for_host(hostid, search=search, limit=limit)
        return TriggersResponse(
            hostid=hostid,
            count=len(triggers),
            triggers=[_trigger_response(trigger) for trigger in triggers],
        )

    # --- Calculo de disponibilidade (o "core" da aplicacao) ---
    # Uma unica trigger, em um unico periodo.
    @router.post(
        "/api/availability/calculate",
        response_model=AvailabilityResultResponse,
        dependencies=protected,
    )
    def calculate_availability_route(
        request: AvailabilityRequest,
        service: AvailabilityService = Depends(get_service),
    ) -> AvailabilityResultResponse:
        return _result_response(service.calculate_trigger(request.triggerid, build_window(request)))

    # Igual ao endpoint acima, mas tambem devolve a linha do tempo
    # (intervalos OK/PROBLEM) e a auditoria do calculo, para depuracao.
    @router.post(
        "/api/availability/timeline",
        response_model=TimelineResponse,
        dependencies=protected,
    )
    def availability_timeline(
        request: AvailabilityRequest,
        service: AvailabilityService = Depends(get_service),
    ) -> TimelineResponse:
        calculation = service.calculate_trigger(request.triggerid, build_window(request))
        return TimelineResponse(
            result=_result_response(calculation),
            audit=CalculationAuditResponse(**asdict(calculation.audit)),
            intervals=[TimelineIntervalResponse(**asdict(interval)) for interval in calculation.timeline],
        )

    # Todas as triggers de um host, calculadas de uma vez.
    @router.post(
        "/api/availability/host/calculate",
        response_model=HostAvailabilityResponse,
        dependencies=protected,
    )
    def calculate_host_availability(
        request: HostAvailabilityRequest,
        service: AvailabilityService = Depends(get_service),
    ) -> HostAvailabilityResponse:
        calculations = service.calculate_host(
            request.hostid,
            window=build_window(request),
            search=request.search,
            limit=request.limit,
        )
        return HostAvailabilityResponse(
            hostid=request.hostid,
            count=len(calculations),
            results=[_result_response(calculation) for calculation in calculations],
        )

    # Fluxo principal da tela: varias triggers (de varios hosts/grupos)
    # agrupadas por "tipo de trigger", com resumo (media/melhor/pior).
    @router.post(
        "/api/availability/group-trigger/calculate",
        response_model=GroupTriggerAvailabilityResponse,
        dependencies=protected,
    )
    def calculate_group_trigger_availability(
        request: GroupTriggerAvailabilityRequest,
        service: AvailabilityService = Depends(get_service),
    ) -> GroupTriggerAvailabilityResponse:
        calculation = service.calculate_trigger_groups(
            request.trigger_keys,
            window=build_window(request),
            group_ids=request.groupids,
            host_ids=request.hostids,
            host_limit=request.host_limit,
            trigger_limit=request.trigger_limit,
        )
        return _group_trigger_availability_response(calculation)

    return router


# --- Conversores: modelos internos (dataclasses de services/) -> schemas
# de resposta da API (Pydantic). Mantidos separados para o frontend nunca
# depender do formato interno do backend.
def _hostgroup_response(group: HostGroupSummary) -> HostGroupResponse:
    return HostGroupResponse(**asdict(group))


def _host_response(host: HostSummary) -> HostResponse:
    return HostResponse(**asdict(host))


def _trigger_response(trigger: TriggerSummary) -> TriggerResponse:
    return TriggerResponse(
        triggerid=trigger.triggerid,
        description=trigger.description,
        status=trigger.status,
        value=trigger.value,
        hosts=[_host_response(host) for host in trigger.hosts],
        grouping_source=trigger.grouping_source,
        grouping_source_id=trigger.grouping_source_id,
        grouping_label=trigger.grouping_label,
    )


def _trigger_group_response(trigger: TriggerGroupSummary) -> TriggerGroupResponse:
    return TriggerGroupResponse(
        key=trigger.key,
        description=trigger.description,
        grouping_source=trigger.grouping_source,
        grouping_source_id=trigger.grouping_source_id,
        grouping_label=trigger.grouping_label,
        trigger_count=trigger.trigger_count,
        host_count=trigger.host_count,
        hosts=[_host_response(host) for host in trigger.hosts],
    )


def _result_response(calculation: TriggerAvailability) -> AvailabilityResultResponse:
    return AvailabilityResultResponse(
        **asdict(calculation.result),
        hosts=[_host_response(host) for host in calculation.trigger.hosts],
        grouping_source=calculation.trigger.grouping_source,
        grouping_source_id=calculation.trigger.grouping_source_id,
        grouping_label=calculation.trigger.grouping_label,
    )


def _group_trigger_availability_response(calculation: GroupTriggerAvailability) -> GroupTriggerAvailabilityResponse:
    return GroupTriggerAvailabilityResponse(
        key=calculation.key,
        description=calculation.description,
        grouping_source=calculation.grouping_source,
        grouping_source_id=calculation.grouping_source_id,
        grouping_label=calculation.grouping_label,
        host_count=calculation.host_count,
        calculated_count=calculation.calculated_count,
        ok_count=calculation.ok_count,
        partial_count=calculation.partial_count,
        inconclusive_count=calculation.inconclusive_count,
        average_availability_percent=calculation.average_availability_percent,
        worst_availability_percent=calculation.worst_availability_percent,
        best_availability_percent=calculation.best_availability_percent,
        total_incident_count=calculation.total_incident_count,
        max_problem_seconds=calculation.max_problem_seconds,
        results=[_result_response(result) for result in calculation.results],
    )
