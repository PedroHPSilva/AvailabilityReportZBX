# Zabbix Availability Backend

Backend Python para calcular disponibilidade de triggers do Zabbix 6.4 usando API JSON-RPC.

## Escopo

- API FastAPI e CLI de validação.
- Consulta somente `user.login`, `hostgroup.get`, `host.get`, `trigger.get`, `triggerprototype.get` e `event.get`.
- Sem banco de dados.
- Sem alterações no Zabbix.
- Execução prevista localmente no servidor do cliente onde o Zabbix está alocado.
- O frontend autentica com usuário e senha do Zabbix; senha não é armazenada e o token fica somente na sessão em memória do backend.

## Estrutura

| Caminho | Responsabilidade |
|---|---|
| `src/integrations/zabbix_client.py` | JSON-RPC com Zabbix |
| `src/domain/availability_calculator.py` | Regra de timeline e disponibilidade |
| `src/services/availability_service.py` | Casos de uso e contratos |
| `src/api/` | App FastAPI, rotas, schemas e dependências |
| `src/main.py` | CLI e exportações CSV |

## Configuração

Crie `.env` a partir de `.env.example`.

```dotenv
ZABBIX_URL=http://127.0.0.1/zabbix/api_jsonrpc.php
# SESSION_TTL_SECONDS=28800
# SESSION_COOKIE_SECURE=false
```

Para uso web, somente `ZABBIX_URL` é obrigatório no backend. Usuário e senha são informados na tela de login.

As variáveis `ZABBIX_USERNAME`, `ZABBIX_PASSWORD`, trigger e período ficam restritas à execução CLI da PoC.

A sessão não sobrevive ao reinício do backend e nenhuma credencial é persistida.

O backend aplica a regra compatível com o Availability report do Zabbix: quando não existe event anterior ao período, o trecho inicial é contabilizado como disponível.

## Instalação

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## API Local

```powershell
python -m uvicorn src.api:app --host 127.0.0.1 --port 8000
```

| Método | Rota |
|---|---|
| GET | `/health` |
| POST | `/api/auth/login` |
| GET | `/api/auth/session` |
| POST | `/api/auth/logout` |
| GET | `/api/filters` |
| GET | `/api/hostgroups` |
| GET | `/api/hosts` |
| GET | `/api/triggers` |
| GET | `/api/hosts/{hostid}/triggers` |
| POST | `/api/availability/calculate` |
| POST | `/api/availability/timeline` |
| POST | `/api/availability/host/calculate` |
| POST | `/api/availability/group-trigger/calculate` |

Docs locais: `http://127.0.0.1:8000/docs`.

As rotas `/api/*`, exceto `/api/auth/*`, exigem sessão autenticada pelo login do Zabbix.

## Identificação de Triggers

- A seleção retorna cada trigger real individualmente, usando a descrição recebida do Zabbix.
- Triggers descobertas, como volumes `C:\`, `D:\` e `E:\`, permanecem separadas.
- A origem por template/prototype continua disponível nos detalhes técnicos para auditoria.

`/api/availability/timeline` também retorna dados de auditoria: existência de event anterior, quantidade de events na janela, origem do estado inicial e manutenção considerada.

## CLI

```powershell
python -m src.main list-hosts --search "Zabbix" --limit 20
python -m src.main list-triggers --host-id 10084 --search "value cache" --limit 5
python -m src.main calculate --trigger-id 13075
python -m src.main calculate-host --host-id 10084 --search "value cache" --limit 2
```

## Testes

```powershell
python -m unittest tests.test_availability_calculator tests.test_availability_service tests.test_api
```
