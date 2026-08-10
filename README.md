# Zabbix Automation

Aplicação local para calcular disponibilidade de hosts/triggers do Zabbix usando a API JSON-RPC.

O modelo previsto para o cliente é execução local no próprio servidor onde o Zabbix está alocado, sem deploy externo.

## Pastas

| Pasta | Função |
|---|---|
| `backend/` | API FastAPI, CLI, cálculo de disponibilidade e integração read-only com Zabbix |
| `frontend/` | Cliente React para consulta, cálculo e exportação |
| `scripts/` | Scripts PowerShell para iniciar e parar backend/frontend |
| `logs/` | Logs e arquivos PID gerados pelos scripts |

## Execução Local

Na raiz do projeto:

```powershell
.\scripts\start_all.ps1
```

URLs padrão:

- Frontend: `http://127.0.0.1:3000`
- Backend: `http://127.0.0.1:8000`

Para parar:

```powershell
.\scripts\stop_all.ps1
```

## Configuração

Backend:

```text
backend/.env
```

Frontend:

```text
frontend/.env
```

Use `GUIA_PILOTO.md` para instalação, configuração, uso e comparação com o relatório do Zabbix.

Use `ENTREGA_LOCAL.md` para preparar o pacote que será levado ao servidor do cliente.

## Verificação Local

Na raiz do projeto:

```powershell
.\scripts\check_local.ps1
```

## Logs

- `logs/backend.log`
- `logs/backend.error.log`
- `logs/frontend.log`
- `logs/frontend.error.log`
