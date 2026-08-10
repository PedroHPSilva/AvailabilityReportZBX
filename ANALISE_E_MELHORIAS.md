# Análise do projeto e melhorias propostas

Este documento resume uma revisão do código (`backend/` e `frontend/`) e
separa o que já foi **implementado nesta rodada** do que fica como
**recomendação para o futuro** (para não misturar mudança de escopo grande
com o que foi pedido agora).

## O que já foi implementado nesta rodada

1. **Logging estruturado no backend** (`backend/src/core/logging_config.py`):
   - Todo request HTTP ganha um `request_id` (reaproveitado do header
     `X-Request-Id` enviado pelo frontend, ou gerado no backend se não vier).
   - Esse ID aparece em toda linha de log relacionada à requisição, no
     header de resposta `X-Request-Id` e no corpo JSON de erro
     (`{"request_id": "..."}`), fechando o ciclo: usuário vê o erro na tela
     com um ID → você grepa esse ID em `logs/app.log`/`logs/app-error.log` e
     encontra o stack trace completo.
   - `logs/app.log` (INFO+) e `logs/app-error.log` (ERROR+, com stack trace)
     substituem a situação anterior, em que erros da aplicação e falhas ao
     falar com o Zabbix não deixavam rastro nenhum além do access log padrão
     do uvicorn (`GET /api/x 200 OK`).
   - `zabbix_client.py` agora loga cada chamada JSON-RPC ao Zabbix com
     método, duração e, em caso de erro, o motivo exato (timeout, HTTP,
     JSON-RPC error da própria API do Zabbix, resposta malformada).
   - Um `ValueError` de validação de negócio (ex.: "trigger não encontrada
     para os filtros informados") deixou de cair no handler genérico de
     erro 500 (que escondia a mensagem real) e agora vira um 400 com a
     mensagem de fato — tanto no log quanto na resposta ao frontend.

2. **Logging/mensagens de erro mais claras no frontend** (`frontend/src/logger.ts`,
   `frontend/src/api.ts`, `App.tsx`):
   - Cada chamada à API gera um `request_id` no navegador, loga no console
     (`console.error`/`warn`/`info`) com contexto estruturado (método, path,
     status, duração, request id) e envia esse ID ao backend.
   - As mensagens de erro exibidas na tela agora incluem, quando fizer
     sentido, o `(ID: ...)` para correlação com o suporte, e o detalhe cru
     devolvido pelo Zabbix em erros 502 (ex.: "Detalhe: No permissions to
     referred object...").

3. **Filtros salvos** (`App.tsx`): grupos, hosts e triggers selecionados
   podem ser salvos com um nome, reaplicados com um clique e excluídos. Fica
   persistido em `localStorage` do navegador (ver limitações abaixo).

4. **Módulo de menu do Zabbix** (`zabbix-module/Availability/`): adiciona
   "Relatórios → Disponibilidade" dentro do próprio Zabbix, abrindo a
   aplicação num iframe.

## Pontos fortes do código (mantidos como estavam)

- Separação clara de camadas: `integrations` (cliente HTTP do Zabbix) →
  `domain` (regra pura de cálculo de disponibilidade) → `services`
  (orquestração) → `api` (HTTP). Isso facilitou muito plugar logging sem
  tocar a lógica de negócio.
- `domain/availability_calculator.py` é puro (sem I/O), o que já é testável
  por unidade — e há testes cobrindo isso (`backend/tests/`).
- O uso de `Protocol` (`ZabbixReadClient`) para desacoplar `AvailabilityService`
  do cliente HTTP concreto é uma boa prática, permite mocks limpos nos testes.
- Tratamento de sessão via cookie `HttpOnly` + `SameSite=Lax` é uma escolha
  razoável para uma aplicação interna.

## Recomendações para próximas iterações (não implementadas agora)

Nada abaixo é urgente, mas vale planejar:

### Backend

- **Sessões em memória não escalam horizontalmente.** `InMemorySessionStore`
  funciona bem com `uvicorn` de 1 processo (o cenário atual), mas se um dia
  rodarem com `--workers > 1` ou múltiplas instâncias atrás de um load
  balancer, cada processo teria sua própria lista de sessões e o login
  "sumiria" ao trocar de worker. Se isso vier a ser necessário, mover para
  Redis (ou outro store compartilhado) resolve.
- **Sem rate limiting no login.** `/api/auth/login` aceita tentativas
  ilimitadas; um script poderia tentar força bruta contra o Zabbix através
  dele. Vale um limitador simples por IP (ex.: `slowapi`) nesse endpoint.
- **Dependência não usada:** `httpx` está em `requirements.txt` mas o
  cliente Zabbix usa `requests`. Pode ser removida.
- **Listagens sem paginação real** (`/api/hosts`, `/api/hostgroups`,
  `/api/triggers` — `default_list_limit`/`max_list_limit` retornam `0`, ou
  seja, "sem limite"). Em uma instalação Zabbix com muitos milhares de hosts,
  isso pode gerar respostas grandes e lentas. Vale considerar paginação por
  cursor/offset se isso acontecer na prática.
- **Cache leve para grupos/hosts**, que mudam pouco: hoje toda abertura da
  tela de filtros refaz `hostgroup.get`/`host.get` no Zabbix. Um cache de
  poucos minutos reduziria a carga na API do Zabbix sem prejudicar a
  atualidade dos dados.

### Frontend

- **`App.tsx` está com ~1700 linhas em um único arquivo.** Funciona, mas
  dificulta manutenção. Uma divisão natural: `components/GroupHostScreen.tsx`,
  `components/SavedFiltersBar.tsx`, `components/PickerBox.tsx`,
  `components/ResultPanels.tsx`, `lib/pdfExport.ts`, `lib/csvExport.ts`,
  mantendo `App.tsx` só como orquestrador. Não fiz essa refatoração agora
  para não gerar um diff gigante misturado com as mudanças funcionais
  pedidas — mas é a próxima melhoria estrutural mais valiosa.
- **Geração de PDF "na mão"** (`buildAvailabilityPdf`, `encodePdf` etc.)
  monta o arquivo PDF manualmente como string. Funciona, mas qualquer ajuste
  futuro de layout é arriscado. Migrar para uma lib como `jspdf` deixaria
  isso mais seguro e legível.
- **Filtros salvos ficam por navegador/máquina** (localStorage), não
  sincronizam entre computadores nem entre usuários. Se isso for importante,
  o próximo passo natural é um endpoint `/api/filters/saved` no backend
  associado à sessão/usuário do Zabbix, com o mesmo modelo de dados que já
  existe no frontend hoje.
- **Sem testes automatizados no frontend.** O backend tem
  `backend/tests/`; o frontend não tem nenhum `*.test.tsx`. Mesmo alguns
  testes de fumaça com Vitest + Testing Library cobrindo o fluxo de login e
  cálculo de disponibilidade já reduziriam bastante o risco de regressão.

### Segurança / operação

- Já coberto no deploy (`deploy/README.md`): considerar HTTPS via
  `certbot --nginx` quando o servidor tiver um domínio.
- Sem SSO real entre Zabbix e a aplicação (login duplicado) — documentado em
  `zabbix-module/Availability/README.md`, junto com o motivo de não ter sido
  implementado agora (complexidade/risco de segurança de um token
  compartilhado mal implementado é maior que o ganho de conveniência).
