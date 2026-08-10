from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ..domain.availability_calculator import AvailabilityResult, TimelineInterval, calculate_availability
from ..integrations.zabbix_client import ZabbixEvent


# Interface minima que o AvailabilityService precisa do cliente Zabbix.
# Usar Protocol (em vez de importar ZabbixClient direto) permite testar
# o service com um cliente falso, sem rede (ver backend/tests/).
class ZabbixReadClient(Protocol):
    def get_hosts(self, search: str | None = None, limit: int | None = None) -> list[dict[str, object]]: ...

    def get_hostgroups(self, search: str | None = None, limit: int | None = None) -> list[dict[str, object]]: ...

    def get_hosts_by_groups(
        self,
        group_ids: list[str],
        search: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, object]]: ...

    def get_trigger(self, trigger_id: str) -> dict[str, object]: ...

    def get_triggers_for_host(
        self,
        host_id: str,
        search: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, object]]: ...

    def get_triggers_for_hosts(
        self,
        host_ids: list[str],
        search: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, object]]: ...

    def get_last_event_before(self, trigger_id: str, period_start_epoch: int) -> ZabbixEvent | None: ...

    def get_events_in_window(
        self,
        trigger_id: str,
        period_start_epoch: int,
        period_end_epoch: int,
    ) -> list[ZabbixEvent]: ...


@dataclass(frozen=True)
# --- "Modelos" internos do service (dataclasses), independentes do
# formato bruto retornado pela API do Zabbix e do formato de resposta
# HTTP (que fica em api/schemas.py). ---
class HostSummary:
    hostid: str
    name: str
    host: str


@dataclass(frozen=True)
class HostGroupSummary:
    groupid: str
    name: str


@dataclass(frozen=True)
class TriggerSummary:
    triggerid: str
    description: str
    status: str
    value: str
    hosts: list[HostSummary]
    grouping_source: str
    grouping_source_id: str
    grouping_label: str


@dataclass(frozen=True)
# Periodo (com timezone) para o qual a disponibilidade sera calculada.
class AvailabilityWindow:
    period_start: datetime
    period_end: datetime
    timezone_name: str
    calculated_at: datetime
    unknown_initial_state_policy: str


@dataclass(frozen=True)
# Resultado do calculo de UMA trigger: a trigger em si + o resultado do
# dominio (domain/availability_calculator.py) + a auditoria.
class TriggerAvailability:
    trigger: TriggerSummary
    result: AvailabilityResult
    timeline: list[TimelineInterval]
    audit: CalculationAudit


@dataclass(frozen=True)
class CalculationAudit:
    previous_event_found: bool
    previous_eventid: str
    events_in_window_count: int
    timeline_intervals_count: int
    initial_state_source: str
    maintenance_considered: bool


@dataclass(frozen=True)
# Um "tipo de trigger" (mesma descricao) agrupando varios hosts -- e o
# que a tela de filtros usa para deixar o usuario escolher por padrao
# de trigger em vez de host por host.
class TriggerGroupSummary:
    key: str
    description: str
    grouping_source: str
    grouping_source_id: str
    grouping_label: str
    trigger_count: int
    host_count: int
    hosts: list[HostSummary]
    triggers: list[TriggerSummary]


@dataclass(frozen=True)
# Resultado agregado de um grupo de triggers: media/melhor/pior
# percentual entre os hosts, alem da lista de resultados individuais.
class GroupTriggerAvailability:
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
    results: list[TriggerAvailability]


# Orquestra as chamadas ao Zabbix (via ZabbixReadClient) e ao calculo
# puro de dominio (availability_calculator.py). E' a camada usada
# diretamente pelas rotas HTTP (api/routes.py) e pela CLI (main.py).
class AvailabilityService:
    def __init__(self, client: ZabbixReadClient) -> None:
        self.client = client

    def list_hosts(self, search: str | None = None, limit: int | None = None) -> list[HostSummary]:
        return [_host_summary(host) for host in self.client.get_hosts(search=search, limit=limit)]

    def list_hostgroups(self, search: str | None = None, limit: int | None = None) -> list[HostGroupSummary]:
        return [_hostgroup_summary(group) for group in self.client.get_hostgroups(search=search, limit=limit)]

    def list_hosts_by_groups(
        self,
        group_ids: list[str],
        search: str | None = None,
        limit: int | None = None,
    ) -> list[HostSummary]:
        if not group_ids:
            return []
        return [
            _host_summary(host)
            for host in self.client.get_hosts_by_groups(group_ids, search=search, limit=limit)
        ]

    def list_triggers_for_host(
        self,
        host_id: str,
        search: str | None = None,
        limit: int | None = None,
    ) -> list[TriggerSummary]:
        return [
            _trigger_summary(trigger)
            for trigger in self.client.get_triggers_for_host(host_id, search=search, limit=limit)
        ]

    # Calcula a disponibilidade de uma unica trigger no periodo informado.
    def calculate_trigger(self, trigger_id: str, window: AvailabilityWindow) -> TriggerAvailability:
        return self._calculate_trigger(_trigger_summary(self.client.get_trigger(trigger_id)), window)

    # Calcula a disponibilidade de todas as triggers de um host de uma vez.
    def calculate_host(
        self,
        host_id: str,
        window: AvailabilityWindow,
        search: str | None = None,
        limit: int = 100,
    ) -> list[TriggerAvailability]:
        triggers = self.list_triggers_for_host(host_id, search=search, limit=limit)
        return [self._calculate_trigger(trigger, window) for trigger in triggers]

    # Lista as triggers dos hosts/grupos filtrados, ja agrupadas por
    # "tipo de trigger" (ver _group_triggers()).
    def list_trigger_groups(
        self,
        group_ids: list[str] | None = None,
        host_ids: list[str] | None = None,
        search: str | None = None,
        host_limit: int | None = None,
        trigger_limit: int | None = None,
    ) -> list[TriggerGroupSummary]:
        resolved_host_ids = self._resolve_host_ids(group_ids=group_ids or [], host_ids=host_ids or [], limit=host_limit)
        if not resolved_host_ids:
            return []
        raw_triggers = self.client.get_triggers_for_hosts(resolved_host_ids, search=search, limit=trigger_limit)
        if search and not raw_triggers:
            raw_triggers = [
                trigger
                for trigger in self.client.get_triggers_for_hosts(resolved_host_ids, limit=trigger_limit)
                if _matches_trigger_search(_trigger_summary(trigger), search)
            ]
        triggers = [_trigger_summary(trigger) for trigger in raw_triggers]
        if search and triggers:
            matched_keys = {_trigger_group_key(trigger) for trigger in triggers}
            all_triggers = [
                _trigger_summary(trigger)
                for trigger in self.client.get_triggers_for_hosts(resolved_host_ids, limit=trigger_limit)
            ]
            triggers = [trigger for trigger in all_triggers if _trigger_group_key(trigger) in matched_keys]
        return _group_triggers(triggers)

    # Calcula um unico grupo de triggers (uma "linha" da tela de resultado).
    def calculate_trigger_group(
        self,
        trigger_key: str,
        window: AvailabilityWindow,
        group_ids: list[str] | None = None,
        host_ids: list[str] | None = None,
        host_limit: int | None = None,
        trigger_limit: int | None = None,
    ) -> GroupTriggerAvailability:
        return self.calculate_trigger_groups(
            [trigger_key],
            window=window,
            group_ids=group_ids,
            host_ids=host_ids,
            host_limit=host_limit,
            trigger_limit=trigger_limit,
        )

    # Calcula varios grupos de trigger de uma vez -- fluxo principal
    # disparado pelo botao "Calcular" da tela de filtros.
    def calculate_trigger_groups(
        self,
        trigger_keys: list[str],
        window: AvailabilityWindow,
        group_ids: list[str] | None = None,
        host_ids: list[str] | None = None,
        host_limit: int | None = None,
        trigger_limit: int | None = None,
    ) -> GroupTriggerAvailability:
        trigger_groups = self.list_trigger_groups(
            group_ids=group_ids,
            host_ids=host_ids,
            host_limit=host_limit,
            trigger_limit=trigger_limit,
        )
        selected_by_key = {group.key: group for group in trigger_groups}
        selected = [selected_by_key[key] for key in trigger_keys if key in selected_by_key]
        if len(selected) != len(set(trigger_keys)):
            raise ValueError("Uma ou mais triggers nao foram encontradas para os filtros informados.")

        results = [
            self._calculate_trigger(trigger, window)
            for group in selected
            for trigger in group.triggers
        ]
        return _selection_availability(selected, results)

    # Busca no Zabbix o evento anterior ao periodo + os eventos dentro do
    # periodo, e delega o calculo em si para o modulo de dominio (puro).
    def _calculate_trigger(self, trigger: TriggerSummary, window: AvailabilityWindow) -> TriggerAvailability:
        previous_event = self.client.get_last_event_before(
            trigger.triggerid,
            int(window.period_start.timestamp()),
        )
        events_in_window = self.client.get_events_in_window(
            trigger.triggerid,
            int(window.period_start.timestamp()),
            int(window.period_end.timestamp()),
        )
        result, timeline = calculate_availability(
            triggerid=trigger.triggerid,
            trigger_name=trigger.description,
            period_start=window.period_start,
            period_end=window.period_end,
            timezone_name=window.timezone_name,
            previous_event=previous_event,
            events_in_window=events_in_window,
            calculated_at=window.calculated_at,
            unknown_initial_state_policy=window.unknown_initial_state_policy,
        )
        return TriggerAvailability(
            trigger=trigger,
            result=result,
            timeline=timeline,
            audit=CalculationAudit(
                previous_event_found=previous_event is not None,
                previous_eventid=previous_event.eventid if previous_event else "",
                events_in_window_count=len(events_in_window),
                timeline_intervals_count=len(timeline),
                initial_state_source=_initial_state_source(previous_event, result.initial_state),
                maintenance_considered=False,
            ),
        )

    def _resolve_host_ids(self, group_ids: list[str], host_ids: list[str], limit: int | None) -> list[str]:
        resolved = [host_id for host_id in host_ids if host_id]
        if group_ids:
            group_hosts = self.list_hosts_by_groups(group_ids, limit=limit)
            group_host_ids = [host.hostid for host in group_hosts if host.hostid]
            resolved = [host_id for host_id in group_host_ids if not resolved or host_id in resolved]
        unique = list(dict.fromkeys(resolved))
        return unique[:limit] if limit is not None else unique


# --- Conversores: dict cru vindo da API do Zabbix -> dataclasses acima ---
def _host_summary(host: dict[str, object]) -> HostSummary:
    return HostSummary(
        hostid=str(host.get("hostid") or ""),
        name=str(host.get("name") or ""),
        host=str(host.get("host") or ""),
    )


def _hostgroup_summary(group: dict[str, object]) -> HostGroupSummary:
    return HostGroupSummary(
        groupid=str(group.get("groupid") or ""),
        name=str(group.get("name") or ""),
    )


def _trigger_summary(trigger: dict[str, object]) -> TriggerSummary:
    raw_hosts = trigger.get("hosts")
    hosts = [_host_summary(host) for host in raw_hosts if isinstance(host, dict)] if isinstance(raw_hosts, list) else []
    grouping_source, grouping_source_id, grouping_label = _trigger_origin(trigger)
    return TriggerSummary(
        triggerid=str(trigger.get("triggerid") or ""),
        description=str(trigger.get("description") or ""),
        status=str(trigger.get("status") or ""),
        value=str(trigger.get("value") or ""),
        hosts=hosts,
        grouping_source=grouping_source,
        grouping_source_id=grouping_source_id,
        grouping_label=grouping_label,
    )


# Agrupa triggers pela mesma "origem" (descricao/template) para exibir
# um item por "tipo de trigger" em vez de um item por host na tela de
# filtros (ex.: "CPU alta" aparece uma vez, cobrindo N hosts).
def _group_triggers(triggers: list[TriggerSummary]) -> list[TriggerGroupSummary]:
    grouped: dict[str, list[TriggerSummary]] = {}
    for trigger in triggers:
        key = _trigger_group_key(trigger)
        grouped.setdefault(key, []).append(trigger)

    summaries: list[TriggerGroupSummary] = []
    for key, group_triggers in grouped.items():
        hosts_by_id: dict[str, HostSummary] = {}
        for trigger in group_triggers:
            for host in trigger.hosts:
                if host.hostid:
                    hosts_by_id[host.hostid] = host
        summaries.append(
            TriggerGroupSummary(
                key=key,
                description=_display_trigger_group_description(group_triggers[0]),
                grouping_source=group_triggers[0].grouping_source,
                grouping_source_id=group_triggers[0].grouping_source_id,
                grouping_label=group_triggers[0].grouping_label,
                trigger_count=len(group_triggers),
                host_count=len(hosts_by_id),
                hosts=sorted(hosts_by_id.values(), key=lambda host: host.name or host.host),
                triggers=sorted(group_triggers, key=lambda trigger: trigger.hosts[0].name if trigger.hosts else ""),
            )
        )
    return sorted(summaries, key=lambda group: group.description)


# Agrega os resultados individuais (por host) em estatisticas do grupo
# (media, melhor, pior, quantos incidentes, etc.).
def _group_availability(
    trigger_group: TriggerGroupSummary,
    results: list[TriggerAvailability],
) -> GroupTriggerAvailability:
    return _selection_availability([trigger_group], results)


def _selection_availability(
    trigger_groups: list[TriggerGroupSummary],
    results: list[TriggerAvailability],
) -> GroupTriggerAvailability:
    primary = trigger_groups[0]
    calculated = [
        result.result.availability_percent
        for result in results
        if result.result.availability_percent is not None
    ]
    ok_count = sum(1 for result in results if result.result.calculation_status == "OK")
    partial_count = sum(1 for result in results if result.result.calculation_status == "PARCIAL")
    inconclusive_count = sum(1 for result in results if result.result.calculation_status == "INCONCLUSIVO")
    average = round(sum(calculated) / len(calculated), 4) if calculated else None
    selected_hosts = {
        host.hostid
        for group in trigger_groups
        for host in group.hosts
        if host.hostid
    }
    is_single = len(trigger_groups) == 1

    return GroupTriggerAvailability(
        key=primary.key if is_single else ",".join(group.key for group in trigger_groups),
        description=primary.description if is_single else f"{len(trigger_groups)} triggers selecionadas",
        grouping_source=primary.grouping_source if is_single else "MULTIPLE_TRIGGERS",
        grouping_source_id=primary.grouping_source_id if is_single else "",
        grouping_label=primary.grouping_label if is_single else "",
        host_count=len(selected_hosts),
        calculated_count=len(results),
        ok_count=ok_count,
        partial_count=partial_count,
        inconclusive_count=inconclusive_count,
        average_availability_percent=average,
        worst_availability_percent=round(min(calculated), 4) if calculated else None,
        best_availability_percent=round(max(calculated), 4) if calculated else None,
        total_incident_count=sum(result.result.incident_count for result in results),
        max_problem_seconds=max((result.result.max_problem_seconds for result in results), default=0),
        results=results,
    )


def _trigger_group_key(trigger: TriggerSummary) -> str:
    return f"trigger:{trigger.triggerid}"


def _display_trigger_group_description(trigger: TriggerSummary) -> str:
    return trigger.description


def _matches_trigger_search(trigger: TriggerSummary, search: str) -> bool:
    needle = _search_text(search)
    candidates = [trigger.description]
    candidates.extend(f"{host.name}({host.hostid}) {trigger.description}" for host in trigger.hosts)
    return any(needle in _search_text(candidate) for candidate in candidates)


def _search_text(value: str) -> str:
    return " ".join(value.split()).casefold()


def _trigger_origin(trigger: dict[str, object]) -> tuple[str, str, str]:
    prototype = trigger.get("discoveryPrototype")
    if isinstance(prototype, dict):
        prototype_id = str(prototype.get("triggerid") or "")
        template_id = str(prototype.get("templateid") or "")
        label = str(prototype.get("description") or "") or f"Prototype {prototype_id}"
        if template_id and template_id != "0":
            return "DISCOVERY_TEMPLATE_PROTOTYPE", template_id, label
        if prototype_id:
            return "DISCOVERY_PROTOTYPE", prototype_id, label

    template_id = str(trigger.get("templateid") or "")
    if template_id and template_id != "0":
        return "TEMPLATE_TRIGGER", template_id, str(trigger.get("description") or "")

    trigger_id = str(trigger.get("triggerid") or "")
    return "EXACT_TRIGGER", trigger_id, str(trigger.get("description") or "")


def _initial_state_source(previous_event: ZabbixEvent | None, initial_state: str) -> str:
    if previous_event:
        return "PREVIOUS_EVENT"
    if initial_state == "ASSUMED_OK":
        return "ASSUMED_FOR_COMPATIBILITY"
    return "NOT_IDENTIFIED"
