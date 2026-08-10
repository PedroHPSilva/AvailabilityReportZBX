from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import requests
import urllib3

from ..core.logging_config import get_logger

logger = get_logger("zabbix_automation.zabbix_client")


def _resolve_verify_ssl(explicit: bool | None) -> bool:
    if explicit is not None:
        return explicit
    raw = os.getenv("ZABBIX_VERIFY_SSL", "true").strip().lower()
    return raw not in ("false", "0", "no", "off")


class ZabbixClientError(RuntimeError):
    """Base error for Zabbix API access."""


class ZabbixHttpError(ZabbixClientError):
    """Raised when the HTTP transport fails."""


class ZabbixJsonRpcError(ZabbixClientError):
    """Raised when Zabbix returns a JSON-RPC error."""


@dataclass(frozen=True)
class ZabbixEvent:
    eventid: str
    clock: int
    value: int
    objectid: str


class ZabbixClient:
    # Cliente HTTP fino sobre a API JSON-RPC do Zabbix (api_jsonrpc.php).
    # Cada metodo publico aqui corresponde a uma chamada de metodo do Zabbix
    # (host.get, trigger.get, event.get, etc.) com os parametros ja
    # montados para o caso de uso desta aplicacao.
    def __init__(
        self,
        base_url: str,
        timeout_seconds: int = 30,
        verify_ssl: bool | None = None,
    ) -> None:
        self.endpoint = self._build_endpoint(base_url)
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()
        self.verify_ssl = _resolve_verify_ssl(verify_ssl)
        self.session.verify = self.verify_ssl
        if not self.verify_ssl:
            # ZABBIX_VERIFY_SSL=false: uso tipico com certificado
            # autoassinado/interno. Fica registrado no log (nao silencioso)
            # porque desabilita uma protecao de seguranca real (abre espaco
            # para ataques man-in-the-middle na conexao com o Zabbix).
            logger.warning(
                "Verificacao de certificado SSL do Zabbix esta DESABILITADA "
                "(ZABBIX_VERIFY_SSL=false). Use apenas se o Zabbix estiver em "
                "rede confiavel com certificado autoassinado/interno."
            )
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self.auth_token: str | None = None
        self.request_id = 1

    @staticmethod
    def _build_endpoint(base_url: str) -> str:
        normalized = base_url.strip().rstrip("/")
        if normalized.endswith("/api_jsonrpc.php"):
            return normalized
        return f"{normalized}/api_jsonrpc.php"

    def login(self, username: str, password: str) -> None:
        # Autentica no Zabbix (user.login) e guarda o auth_token para as
        # proximas chamadas. Usado no fluxo de login da aplicacao web.
        self.auth_token = self._request(
            "user.login",
            {"username": username, "password": password},
            authenticated=False,
        )

    def use_auth_token(self, auth_token: str) -> None:
        self.auth_token = auth_token

    def check_authentication(self, session_id: str) -> dict[str, Any]:
        # Valida um sessionid ja existente (ex.: o da sessao do usuario
        # logado no proprio frontend do Zabbix -- ver zabbix-module/) e
        # devolve os dados do usuario (userid, username, etc.) se for valido.
        # Levanta ZabbixJsonRpcError se o sessionid for invalido/expirado.
        return self._request(
            "user.checkAuthentication",
            {"sessionid": session_id},
            authenticated=False,
        )

    def get_trigger(self, trigger_id: str) -> dict[str, Any]:
        triggers = self._request(
            "trigger.get",
            {
                "triggerids": [trigger_id],
                "output": ["triggerid", "description", "status", "value", "templateid", "flags"],
                "selectHosts": ["hostid", "host", "name"],
                "selectTriggerDiscovery": ["parent_triggerid"],
            },
        )
        if not triggers:
            raise ZabbixClientError(f"Trigger nao encontrada: {trigger_id}")
        return self._attach_trigger_prototypes(triggers)[0]

    def get_hosts(self, search: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "output": ["hostid", "host", "name"],
            "monitored_hosts": True,
        }
        if limit is not None:
            params["limit"] = limit
        if search:
            params["search"] = {"name": search}

        hosts = self._request("host.get", params)
        return sorted(hosts, key=lambda host: str(host.get("name") or host.get("host") or ""))

    def get_hostgroups(self, search: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "output": ["groupid", "name"],
        }
        if limit is not None:
            params["limit"] = limit
        if search:
            params["search"] = {"name": search}

        groups = self._request("hostgroup.get", params)
        return sorted(groups, key=lambda group: str(group.get("name") or ""))

    def get_hosts_by_groups(
        self,
        group_ids: list[str],
        search: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "groupids": group_ids,
            "output": ["hostid", "host", "name"],
            "monitored_hosts": True,
        }
        if limit is not None:
            params["limit"] = limit
        if search:
            params["search"] = {"name": search}

        hosts = self._request("host.get", params)
        return sorted(hosts, key=lambda host: str(host.get("name") or host.get("host") or ""))

    def get_triggers_for_hosts(
        self,
        host_ids: list[str],
        search: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "hostids": host_ids,
            "output": ["triggerid", "description", "status", "value", "templateid", "flags"],
            "selectHosts": ["hostid", "host", "name"],
            "selectTriggerDiscovery": ["parent_triggerid"],
            "monitored": True,
        }
        if limit is not None:
            params["limit"] = limit
        if search:
            params["search"] = {"description": search}

        triggers = self._attach_trigger_prototypes(self._request("trigger.get", params))
        return sorted(
            triggers,
            key=lambda trigger: (
                str(trigger.get("description") or ""),
                str((trigger.get("hosts") or [{}])[0].get("name") if isinstance(trigger.get("hosts"), list) else ""),
            ),
        )

    def get_triggers_for_host(
        self,
        host_id: str,
        search: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "hostids": [host_id],
            "output": ["triggerid", "description", "status", "value", "templateid", "flags"],
            "selectHosts": ["hostid", "host", "name"],
            "selectTriggerDiscovery": ["parent_triggerid"],
            "monitored": True,
        }
        if limit is not None:
            params["limit"] = limit
        if search:
            params["search"] = {"description": search}

        triggers = self._attach_trigger_prototypes(self._request("trigger.get", params))
        return sorted(triggers, key=lambda trigger: str(trigger.get("description") or ""))

    def _attach_trigger_prototypes(self, triggers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        parent_ids = {
            str(discovery["parent_triggerid"])
            for trigger in triggers
            if isinstance((discovery := trigger.get("triggerDiscovery")), dict)
            and discovery.get("parent_triggerid")
        }
        if not parent_ids:
            return triggers

        prototypes = self._request(
            "triggerprototype.get",
            {
                "triggerids": sorted(parent_ids),
                "output": ["triggerid", "description", "templateid"],
            },
        )
        prototypes_by_id = {str(prototype.get("triggerid")): prototype for prototype in prototypes}
        for trigger in triggers:
            discovery = trigger.get("triggerDiscovery")
            if isinstance(discovery, dict):
                prototype = prototypes_by_id.get(str(discovery.get("parent_triggerid") or ""))
                if prototype:
                    trigger["discoveryPrototype"] = prototype
        return triggers

    def get_last_event_before(self, trigger_id: str, period_start_epoch: int) -> ZabbixEvent | None:
        if period_start_epoch <= 0:
            return None

        events = self._request(
            "event.get",
            {
                "source": 0,
                "object": 0,
                "objectids": [trigger_id],
                "time_till": period_start_epoch - 1,
                "output": ["eventid", "clock", "value", "objectid"],
                "sortfield": ["clock", "eventid"],
                "sortorder": "DESC",
                "limit": 1,
            },
        )
        return self._to_event(events[0]) if events else None

    def get_events_in_window(
        self,
        trigger_id: str,
        period_start_epoch: int,
        period_end_epoch: int,
    ) -> list[ZabbixEvent]:
        events = self._request(
            "event.get",
            {
                "source": 0,
                "object": 0,
                "objectids": [trigger_id],
                "time_from": period_start_epoch,
                "time_till": period_end_epoch,
                "output": ["eventid", "clock", "value", "objectid"],
                "sortfield": ["clock", "eventid"],
                "sortorder": "ASC",
            },
        )
        parsed_events = [self._to_event(event) for event in events]
        return sorted(parsed_events, key=lambda event: (event.clock, int(event.eventid)))

    def _request(
        self,
        method: str,
        params: dict[str, Any],
        authenticated: bool = True,
    ) -> Any:
        # Ponto unico por onde toda chamada JSON-RPC ao Zabbix passa: monta o
        # payload, faz o POST, e trata os 3 jeitos de dar errado (falha de
        # rede/SSL, resposta que nao e JSON, e erro JSON-RPC do proprio
        # Zabbix) -- cada um logado de forma diferente (ver logs/app*.log).
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": self.request_id,
        }
        self.request_id += 1

        if authenticated:
            if not self.auth_token:
                logger.error("Chamada a %s sem cliente autenticado.", method)
                raise ZabbixClientError("Cliente Zabbix nao autenticado.")
            payload["auth"] = self.auth_token

        started_at = time.monotonic()
        try:
            response = self.session.post(
                self.endpoint,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except requests.exceptions.SSLError as exc:
            duration_ms = round((time.monotonic() - started_at) * 1000, 1)
            logger.error(
                "Falha de certificado SSL ao chamar %s em %s (%sms): %s",
                method,
                self.endpoint,
                duration_ms,
                exc,
            )
            raise ZabbixHttpError(
                f"Falha de certificado SSL ao chamar {method}: {exc}. "
                "Se o Zabbix usa certificado autoassinado/interno, defina "
                "ZABBIX_VERIFY_SSL=false no backend/.env."
            ) from exc
        except requests.RequestException as exc:
            duration_ms = round((time.monotonic() - started_at) * 1000, 1)
            status_code = getattr(exc.response, "status_code", None)
            logger.error(
                "Falha HTTP ao chamar %s em %s (status=%s, %sms): %s",
                method,
                self.endpoint,
                status_code,
                duration_ms,
                exc,
            )
            raise ZabbixHttpError(f"Falha HTTP ao chamar {method}: {exc}") from exc

        duration_ms = round((time.monotonic() - started_at) * 1000, 1)

        try:
            body = response.json()
        except ValueError as exc:
            logger.error(
                "Resposta nao-JSON do Zabbix ao chamar %s (status=%s, %sms). Corpo (truncado): %s",
                method,
                response.status_code,
                duration_ms,
                response.text[:500],
            )
            raise ZabbixJsonRpcError(f"Resposta JSON invalida ao chamar {method}.") from exc

        if "error" in body:
            error = body["error"]
            message = error.get("message", "erro JSON-RPC")
            detail = error.get("data", "")
            logger.warning(
                "Zabbix retornou erro JSON-RPC em %s (%sms): %s - %s",
                method,
                duration_ms,
                message,
                detail,
            )
            raise ZabbixJsonRpcError(f"Zabbix {method}: {message}. {detail}".strip())

        if "result" not in body:
            logger.error("Resposta JSON-RPC sem 'result' ao chamar %s (%sms).", method, duration_ms)
            raise ZabbixJsonRpcError(f"Resposta JSON-RPC sem result ao chamar {method}.")

        logger.debug("Chamada %s concluida em %sms.", method, duration_ms)
        return body["result"]

    @staticmethod
    def _to_event(raw_event: dict[str, Any]) -> ZabbixEvent:
        try:
            return ZabbixEvent(
                eventid=str(raw_event["eventid"]),
                clock=int(raw_event["clock"]),
                value=int(raw_event["value"]),
                objectid=str(raw_event["objectid"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ZabbixJsonRpcError("Event retornado pelo Zabbix e invalido.") from exc
