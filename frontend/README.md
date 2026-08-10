# Zabbix Availability Frontend

Cliente React com Tailwind CSS para consulta local de disponibilidade contra a API em `../backend`.

## Tela

- Consulta por grupos, hosts e triggers.
- Seleção individual ou múltipla.
- Login com usuário e senha do próprio Zabbix antes das consultas.
- Resumo de disponibilidade, detalhamento por host e exportação CSV/PDF.

## Escopo Atual

- Modais de seleção múltipla para grupos de hosts, hosts e triggers.
- Seletor de tema claro/escuro com preferência local.
- Linguagem operacional para usuário não técnico.
- Auditoria sob demanda com estado inicial, histórico anterior, eventos considerados e linha do tempo utilizada.
- Gráfico de composição entre disponibilidade e indisponibilidade.
- Ranking visual dos itens com menor disponibilidade.

O navegador recebe somente um cookie de sessão `HttpOnly`; senha e token Zabbix não são mantidos no frontend.

## Configuração

Crie `.env` se precisar sobrescrever a URL da API:

```dotenv
VITE_API_BASE_URL=http://127.0.0.1:8000
```

## Execução

```powershell
npm install
npm run dev
```

URL local: `http://127.0.0.1:3000`.

## Build

```powershell
npm run build
```
