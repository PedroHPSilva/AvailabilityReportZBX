from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

from .domain.availability_calculator import (
    ASSUME_OK_WHEN_NO_PREVIOUS_EVENT,
    AvailabilityResult,
    TimelineInterval,
)
from .integrations.zabbix_client import ZabbixClient, ZabbixClientError
from .services.availability_service import (
    AvailabilityService,
    AvailabilityWindow,
    HostSummary,
    TriggerAvailability,
    TriggerSummary,
)


CONNECTION_ENV_VARS = (
    "ZABBIX_URL",
    "ZABBIX_USERNAME",
    "ZABBIX_PASSWORD",
)

CALCULATION_ENV_VARS = (
    "ZABBIX_TRIGGER_ID",
    "PERIOD_START",
    "PERIOD_END",
    "TIMEZONE",
)

RESULT_FIELDS = (
    "triggerid",
    "trigger_name",
    "period_start",
    "period_end",
    "timezone",
    "initial_state",
    "total_seconds",
    "ok_seconds",
    "problem_seconds",
    "availability_percent",
    "problem_percent",
    "incident_count",
    "max_problem_seconds",
    "calculation_status",
    "maintenance_considered",
    "calculated_at",
)

TIMELINE_FIELDS = (
    "triggerid",
    "interval_start",
    "interval_end",
    "state",
    "duration_seconds",
    "source_eventid",
)

def main() -> int:
    parser = argparse.ArgumentParser(description="PoC controlada de disponibilidade por trigger no Zabbix 6.4.")
    parser.add_argument(
        "command",
        nargs="?",
        default="calculate",
        choices=("calculate", "calculate-host", "list-hosts", "list-triggers"),
        help="Comando a executar. O padrao e calculate.",
    )
    parser.add_argument(
        "--timeline-csv",
        action="store_true",
        help="Gera output/availability_timeline.csv com a linha do tempo usada no calculo.",
    )
    parser.add_argument("--host-id", help="Host ID para list-triggers.")
    parser.add_argument("--search", help="Busca parcial por nome de host ou descricao de trigger.")
    parser.add_argument("--limit", type=int, help="Quantidade maxima de itens retornados em listagens.")
    parser.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
        help="Formato de saida no terminal para listagens e calculos.",
    )
    parser.add_argument(
        "--export-csv",
        action="store_true",
        help="Exporta listagens para CSV no diretorio output.",
    )
    parser.add_argument("--trigger-id", help="Sobrescreve ZABBIX_TRIGGER_ID no calculo.")
    parser.add_argument("--period-start", help="Sobrescreve PERIOD_START no calculo.")
    parser.add_argument("--period-end", help="Sobrescreve PERIOD_END no calculo.")
    parser.add_argument("--timezone", help="Sobrescreve TIMEZONE no calculo.")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    load_dotenv(project_root / ".env")

    try:
        connection_config = _read_connection_config()
        client = ZabbixClient(connection_config["ZABBIX_URL"])
        client.login(connection_config["ZABBIX_USERNAME"], connection_config["ZABBIX_PASSWORD"])
        service = AvailabilityService(client)

        if args.command == "list-hosts":
            hosts = service.list_hosts(search=args.search, limit=_validate_limit(args.limit))
            _emit_hosts(project_root, hosts, args)
            return 0

        if args.command == "list-triggers":
            if not args.host_id:
                raise ValueError("--host-id e obrigatorio para list-triggers.")
            triggers = service.list_triggers_for_host(
                args.host_id,
                search=args.search,
                limit=_validate_limit(args.limit),
            )
            _emit_triggers(project_root, args.host_id, triggers, args)
            return 0

        config = _read_calculation_config(args, require_trigger_id=args.command == "calculate")
        timezone = _load_timezone(config["TIMEZONE"])
        period_start = _parse_datetime(config["PERIOD_START"], timezone, "PERIOD_START")
        period_end = _parse_datetime(config["PERIOD_END"], timezone, "PERIOD_END")
        calculated_at = datetime.now(timezone)
        window = AvailabilityWindow(
            period_start=period_start,
            period_end=period_end,
            timezone_name=config["TIMEZONE"],
            calculated_at=calculated_at,
            unknown_initial_state_policy=ASSUME_OK_WHEN_NO_PREVIOUS_EVENT,
        )

        if args.command == "calculate-host":
            if not args.host_id:
                raise ValueError("--host-id e obrigatorio para calculate-host.")
            calculations = service.calculate_host(
                args.host_id,
                window=window,
                search=args.search,
                limit=_validate_limit(args.limit),
            )
            _emit_host_calculation(project_root, args.host_id, calculations, args)
            return 0

        calculation = service.calculate_trigger(config["ZABBIX_TRIGGER_ID"], window)
        result = calculation.result
        timeline = calculation.timeline

        output_dir = project_root / "output"
        output_dir.mkdir(exist_ok=True)
        _write_result_csv(output_dir / "availability_result.csv", result)
        if args.timeline_csv:
            _write_timeline_csv(output_dir / "availability_timeline.csv", timeline)

        if args.format == "json":
            _print_json(_result_contract(calculation))
        else:
            _print_result(result, _format_trigger_hosts(calculation.trigger.hosts), args.timeline_csv)
        return 0
    except (ValueError, ZoneInfoNotFoundError, ZabbixClientError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1


def _read_connection_config() -> dict[str, str]:
    missing = [name for name in CONNECTION_ENV_VARS if not os.getenv(name)]
    if missing:
        raise ValueError(f"Variaveis obrigatorias ausentes: {', '.join(missing)}")
    return {name: os.environ[name].strip() for name in CONNECTION_ENV_VARS}


def _read_calculation_config(args: argparse.Namespace, require_trigger_id: bool = True) -> dict[str, str]:
    overrides = {
        "ZABBIX_TRIGGER_ID": args.trigger_id,
        "PERIOD_START": args.period_start,
        "PERIOD_END": args.period_end,
        "TIMEZONE": args.timezone,
    }
    required_env_vars = CALCULATION_ENV_VARS if require_trigger_id else CALCULATION_ENV_VARS[1:]
    missing = [
        name
        for name in required_env_vars
        if not (overrides.get(name) or os.getenv(name))
    ]
    if missing:
        raise ValueError(f"Variaveis de calculo ausentes: {', '.join(missing)}")

    config = {}
    for name in CALCULATION_ENV_VARS:
        value = overrides.get(name) or os.getenv(name)
        if value:
            config[name] = str(value).strip()
    return config


def _validate_limit(limit: int | None) -> int | None:
    if limit is None:
        return None
    if limit <= 0:
        raise ValueError("--limit deve ser maior que zero.")
    return limit


def _load_timezone(timezone_name: str) -> ZoneInfo:
    return ZoneInfo(timezone_name)


def _parse_datetime(raw_value: str, timezone: ZoneInfo, name: str) -> datetime:
    normalized = raw_value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{name} deve ser datetime ISO-8601.") from exc

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone)
    return parsed.astimezone(timezone)


def _write_result_csv(path: Path, result: AvailabilityResult) -> None:
    _write_results_csv(path, [result])


def _write_results_csv(path: Path, results: list[AvailabilityResult]) -> None:
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=RESULT_FIELDS)
        writer.writeheader()
        for result in results:
            row = _result_row(result)
            writer.writerow({field: row[field] for field in RESULT_FIELDS})


def _write_timeline_csv(path: Path, timeline: list[TimelineInterval]) -> None:
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=TIMELINE_FIELDS)
        writer.writeheader()
        for interval in timeline:
            writer.writerow(
                {
                    "triggerid": interval.triggerid,
                    "interval_start": interval.interval_start.isoformat(),
                    "interval_end": interval.interval_end.isoformat(),
                    "state": interval.state,
                    "duration_seconds": interval.duration_seconds,
                    "source_eventid": interval.source_eventid,
                }
            )


def _write_rows_csv(path: Path, fieldnames: tuple[str, ...], rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _result_row(result: AvailabilityResult) -> dict[str, object]:
    row = asdict(result)
    row["period_start"] = result.period_start.isoformat()
    row["period_end"] = result.period_end.isoformat()
    row["calculated_at"] = result.calculated_at.isoformat()
    row["availability_percent"] = _csv_percent(result.availability_percent)
    row["problem_percent"] = _csv_percent(result.problem_percent)
    return row


def _host_contract(host: HostSummary) -> dict[str, str]:
    return asdict(host)


def _trigger_contract(trigger: TriggerSummary) -> dict[str, object]:
    row = asdict(trigger)
    row["hosts"] = [_host_contract(host) for host in trigger.hosts]
    return row


def _result_contract(calculation: TriggerAvailability) -> dict[str, object]:
    row = asdict(calculation.result)
    row["period_start"] = calculation.result.period_start.isoformat()
    row["period_end"] = calculation.result.period_end.isoformat()
    row["calculated_at"] = calculation.result.calculated_at.isoformat()
    row["hosts"] = [_host_contract(host) for host in calculation.trigger.hosts]
    return row


def _emit_hosts(project_root: Path, hosts: list[HostSummary], args: argparse.Namespace) -> None:
    if args.export_csv:
        output_path = project_root / "output" / "hosts.csv"
        output_path.parent.mkdir(exist_ok=True)
        _write_rows_csv(output_path, ("hostid", "name", "host"), [_host_contract(host) for host in hosts])

    if args.format == "json":
        _print_json({"count": len(hosts), "hosts": [_host_contract(host) for host in hosts]})
    else:
        _print_hosts(hosts, csv_name="output/hosts.csv" if args.export_csv else None)


def _emit_triggers(
    project_root: Path,
    host_id: str,
    triggers: list[TriggerSummary],
    args: argparse.Namespace,
) -> None:
    if args.export_csv:
        output_path = project_root / "output" / "triggers.csv"
        output_path.parent.mkdir(exist_ok=True)
        rows = [
            {
                "triggerid": trigger.triggerid,
                "description": trigger.description,
                "status": trigger.status,
                "value": trigger.value,
                "hostids": ",".join(host.hostid for host in trigger.hosts),
            }
            for trigger in triggers
        ]
        _write_rows_csv(output_path, ("triggerid", "description", "status", "value", "hostids"), rows)

    if args.format == "json":
        _print_json({"hostid": host_id, "count": len(triggers), "triggers": [_trigger_contract(trigger) for trigger in triggers]})
    else:
        _print_triggers(
            host_id,
            triggers,
            csv_name="output/triggers.csv" if args.export_csv else None,
        )


def _emit_host_calculation(
    project_root: Path,
    host_id: str,
    calculations: list[TriggerAvailability],
    args: argparse.Namespace,
) -> None:
    output_dir = project_root / "output"
    output_dir.mkdir(exist_ok=True)
    results = [calculation.result for calculation in calculations]
    timelines = [interval for calculation in calculations for interval in calculation.timeline]
    _write_results_csv(output_dir / "availability_results.csv", results)
    if args.timeline_csv:
        _write_timeline_csv(output_dir / "availability_timelines.csv", timelines)

    if args.format == "json":
        _print_json(
            {
                "hostid": host_id,
                "count": len(results),
                "results": [_result_contract(calculation) for calculation in calculations],
            }
        )
        return

    _print_host_results(host_id, results, args.timeline_csv)


def _format_trigger_hosts(hosts: list[HostSummary]) -> str:
    if not hosts:
        return "nao identificado"

    formatted_hosts: list[str] = []
    for host in hosts:
        display_name = host.name or host.host or "sem_nome"
        technical_name = host.host
        hostid = host.hostid
        details = [display_name]
        if technical_name and technical_name != display_name:
            details.append(f"host tecnico: {technical_name}")
        if hostid:
            details.append(f"hostid: {hostid}")
        formatted_hosts.append(" | ".join(details))

    return "; ".join(formatted_hosts) if formatted_hosts else "nao identificado"


def _print_hosts(hosts: list[HostSummary], csv_name: str | None = None) -> None:
    print(f"Hosts encontrados: {len(hosts)}")
    print("hostid | nome exibido | host tecnico")
    for host in hosts:
        print(
            f"{host.hostid} | "
            f"{host.name} | "
            f"{host.host}"
        )
    if csv_name:
        print(f"csv hosts: {csv_name}")


def _print_triggers(host_id: str, triggers: list[TriggerSummary], csv_name: str | None = None) -> None:
    print(f"Triggers encontradas para hostid {host_id}: {len(triggers)}")
    print("triggerid | valor atual | descricao")
    for trigger in triggers:
        print(
            f"{trigger.triggerid} | "
            f"{trigger.value} | "
            f"{trigger.description}"
        )
    if csv_name:
        print(f"csv triggers: {csv_name}")


def _print_host_results(host_id: str, results: list[AvailabilityResult], timeline_csv_generated: bool) -> None:
    print(f"Disponibilidade calculada para hostid {host_id}: {len(results)} trigger(s)")
    print("triggerid | disponibilidade | problem | status | descricao")
    for result in results:
        print(
            f"{result.triggerid} | "
            f"{_terminal_percent(result.availability_percent)} | "
            f"{_terminal_percent(result.problem_percent)} | "
            f"{result.calculation_status} | "
            f"{result.trigger_name}"
        )
    print("csv resultados: output/availability_results.csv")
    if timeline_csv_generated:
        print("csv timelines: output/availability_timelines.csv")


def _print_result(result: AvailabilityResult, trigger_hosts: str, timeline_csv_generated: bool) -> None:
    print("Resultado da PoC")
    print(f"triggerid: {result.triggerid}")
    print(f"nome da trigger: {result.trigger_name}")
    print(f"host da trigger: {trigger_hosts}")
    print(f"periodo analisado: {result.period_start.isoformat()} -> {result.period_end.isoformat()}")
    print(f"timezone: {result.timezone}")
    print(f"estado inicial: {result.initial_state}")
    print(f"tempo total: {result.total_seconds} s")
    print(f"tempo OK: {result.ok_seconds} s")
    print(f"tempo Problem: {result.problem_seconds} s")
    print(f"disponibilidade percentual: {_terminal_percent(result.availability_percent)}")
    print(f"indisponibilidade percentual: {_terminal_percent(result.problem_percent)}")
    print(f"quantidade de incidentes: {result.incident_count}")
    print(f"maior incidente: {result.max_problem_seconds} s")
    print(f"status do calculo: {result.calculation_status}")
    print(f"observacoes: {result.observations}")
    print("csv resultado: output/availability_result.csv")
    if timeline_csv_generated:
        print("csv timeline: output/availability_timeline.csv")


def _csv_percent(value: float | None) -> str | float:
    return "" if value is None else value


def _terminal_percent(value: float | None) -> str:
    return "nao calculado" if value is None else f"{value:.4f}%"


def _print_json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
