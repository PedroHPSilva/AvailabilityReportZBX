# Fase 2 - Resumo Tecnico

## Objetivo

Preparar a PoC de disponibilidade por trigger para evoluir a um backend de dashboard sem criar UI, banco de dados ou operacoes de escrita no Zabbix.

## Entregas realizadas

- Cliente read-only para API JSON-RPC do Zabbix.
- Calculo por `trigger.get` + `event.get`.
- Politica controlada para estado inicial desconhecido.
- Descoberta de hosts e triggers monitoradas.
- Calculo de uma trigger ou triggers de um host com limite.
- Saidas em terminal, JSON e CSV.
- Camada de servico separada da CLI.
- Testes unitarios do calculo e do servico.

## Estrutura principal

| Arquivo | Responsabilidade |
|---|---|
| `src/integrations/zabbix_client.py` | Chamadas read-only ao Zabbix |
| `src/domain/availability_calculator.py` | Timeline e metricas |
| `src/services/availability_service.py` | Contratos e orquestracao de dominio |
| `src/main.py` | CLI e exportacoes |

## Regra atual

- Event anterior define estado inicial.
- Events da janela sao ordenados por `clock` e `eventid`.
- `value = 0` e `OK`; `value = 1` e `PROBLEM`.
- Manutencao nao e considerada.
- Sem event anterior, `ASSUME_OK_WHEN_NO_PREVIOUS_EVENT` assume o trecho inicial como `OK` para comparacao com o frontend e marca `PARCIAL`.

## Contratos base

- Host: `hostid`, `name`, `host`.
- Trigger: `triggerid`, `description`, `status`, `value`, `hosts`.
- Disponibilidade: periodo, tempos OK/Problem, percentuais, incidentes, maior incidente, status e observacoes.

## Validacoes realizadas

- Caso `100% OK` comparado com frontend.
- Caso com incidente comparado com frontend.
- Casos sem event anterior tratados por politica explicita.
- Listagens e calculo por host validados contra o Zabbix configurado.

## Transicao para Fase 3

A API interna foi iniciada em `src/api/` sobre `AvailabilityService`. O dashboard futuro deve consumir essa API para listar hosts, listar triggers e calcular disponibilidade sem receber credenciais Zabbix.
