// Tipos espelhando os schemas de resposta do backend (api/schemas.py).
// Mantidos em um so lugar (este arquivo) para o resto do frontend nao
// duplicar a forma dos dados.
export type Host = {
  hostid: string
  name: string
  host: string
}

export type HostGroup = {
  groupid: string
  name: string
}

export type TriggerGroup = {
  key: string
  description: string
  grouping_source: string
  grouping_source_id: string
  grouping_label: string
  trigger_count: number
  host_count: number
  hosts: Host[]
}

// period_start/period_end sao strings ISO 8601 com segundos (ex.:
// "2026-07-10T23:59:59"), geradas pelo <input type="datetime-local"
// step={1}> em App.tsx -- ver PERIODO_23_59_59.md.
export type AvailabilityRequest = {
  triggerid: string
  period_start: string
  period_end: string
  timezone: string
}

export type AvailabilityResult = {
  triggerid: string
  trigger_name: string
  period_start: string
  period_end: string
  timezone: string
  initial_state: string
  total_seconds: number
  ok_seconds: number
  problem_seconds: number
  availability_percent: number | null
  problem_percent: number | null
  incident_count: number
  max_problem_seconds: number
  calculation_status: string
  maintenance_considered: string
  calculated_at: string
  observations: string
  hosts: Host[]
  grouping_source: string
  grouping_source_id: string
  grouping_label: string
}

export type TimelineInterval = {
  triggerid: string
  interval_start: string
  interval_end: string
  state: string
  duration_seconds: number
  source_eventid: string
}

export type Timeline = {
  result: AvailabilityResult
  audit: CalculationAudit
  intervals: TimelineInterval[]
}

export type CalculationAudit = {
  previous_event_found: boolean
  previous_eventid: string
  events_in_window_count: number
  timeline_intervals_count: number
  initial_state_source: string
  maintenance_considered: boolean
}

export type GroupTriggerAvailabilityRequest = {
  trigger_keys: string[]
  period_start: string
  period_end: string
  timezone: string
  groupids: string[]
  hostids: string[]
}

export type GroupTriggerAvailability = {
  key: string
  description: string
  grouping_source: string
  grouping_source_id: string
  grouping_label: string
  host_count: number
  calculated_count: number
  ok_count: number
  partial_count: number
  inconclusive_count: number
  average_availability_percent: number | null
  worst_availability_percent: number | null
  best_availability_percent: number | null
  total_incident_count: number
  max_problem_seconds: number
  results: AvailabilityResult[]
}

export type ApiError = {
  error?: string
  message?: string
  detail?: string
  details?: unknown
  request_id?: string | null
}

// Erro lancado por request() sempre que a chamada falha (rede ou HTTP
// nao-2xx). Carrega o requestId para correlacionar com os logs do
// backend (logs/app.log / logs/app-error.log).
export class ApiRequestError extends Error {
  status: number
  code?: string
  requestId?: string
  details?: unknown

  constructor(message: string, status: number, code?: string, requestId?: string, details?: unknown) {
    super(message)
    this.name = "ApiRequestError"
    this.status = status
    this.code = code
    this.requestId = requestId
    this.details = details
  }
}

export type AuthSession = {
  authenticated: boolean
  username: string | null
}

import { generateRequestId, logger } from "./logger"

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000"

// --- Funcoes publicas usadas pelo App.tsx (uma por endpoint da API) ---
export async function getSession(): Promise<AuthSession> {
  return request("/api/auth/session")
}

export async function login(username: string, password: string): Promise<AuthSession> {
  return request("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  })
}

// Login sem usuario/senha, usado quando a aplicacao e' aberta dentro do
// Zabbix via o modulo (zabbix-module/): o sessionid vem na URL (?sso=...),
// colocado la pelo Module.php a partir da sessao ja autenticada no Zabbix.
export async function loginWithZabbixSession(sessionId: string): Promise<AuthSession> {
  return request("/api/auth/sso", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId }),
  })
}

export async function logout(): Promise<AuthSession> {
  return request("/api/auth/logout", { method: "POST" })
}

export async function getHostGroups(search: string): Promise<HostGroup[]> {
  const params = new URLSearchParams()
  if (search.trim()) params.set("search", search.trim())
  const data = await request<{ groups: HostGroup[] }>(`/api/hostgroups?${params.toString()}`)
  return data.groups
}

export async function getHosts(search: string, groupids: string[] = []): Promise<Host[]> {
  const params = new URLSearchParams()
  if (search.trim()) params.set("search", search.trim())
  for (const groupid of groupids) params.append("groupids", groupid)
  const data = await request<{ hosts: Host[] }>(`/api/hosts?${params.toString()}`)
  return data.hosts
}

export async function getTriggerGroups(groupids: string[], hostids: string[], search: string): Promise<TriggerGroup[]> {
  const params = new URLSearchParams()
  if (search.trim()) params.set("search", search.trim())
  for (const groupid of groupids) params.append("groupids", groupid)
  for (const hostid of hostids) params.append("hostids", hostid)
  const data = await request<{ triggers: TriggerGroup[] }>(`/api/triggers?${params.toString()}`)
  return data.triggers
}

export async function getTimeline(payload: AvailabilityRequest): Promise<Timeline> {
  return request("/api/availability/timeline", {
    method: "POST",
    body: JSON.stringify(payload),
  })
}

export async function calculateGroupTrigger(
  payload: GroupTriggerAvailabilityRequest,
): Promise<GroupTriggerAvailability> {
  return request("/api/availability/group-trigger/calculate", {
    method: "POST",
    body: JSON.stringify(payload),
  })
}

// Wrapper unico sobre fetch(): adiciona o X-Request-Id, credenciais
// (cookie de sessao), loga cada chamada (ver logger.ts) e converte
// respostas de erro em ApiRequestError com mensagem pronta para exibir.
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const requestId = generateRequestId()
  const method = init?.method ?? "GET"
  const startedAt = performance.now()

  let response: Response
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        "X-Request-Id": requestId,
        ...init?.headers,
      },
    })
  } catch (caught) {
    const durationMs = Math.round(performance.now() - startedAt)
    logger.error(`Falha de rede em ${method} ${path}`, {
      requestId,
      durationMs,
      cause: caught instanceof Error ? caught.message : String(caught),
    })
    throw new ApiRequestError(
      "Nao foi possivel conectar ao backend. Verifique se a API esta em execucao e se o endereco configurado (VITE_API_BASE_URL) esta correto.",
      0,
      "NETWORK_ERROR",
      requestId,
    )
  }

  const durationMs = Math.round(performance.now() - startedAt)
  // O backend ecoa o mesmo X-Request-Id enviado (ver app.py); se por algum
  // motivo nao vier (ex.: erro antes do middleware rodar), usamos o nosso.
  const serverRequestId = response.headers.get("x-request-id") ?? requestId

  if (!response.ok) {
    const data = (await response.json().catch(() => ({}))) as ApiError
    const message = data.message ?? data.detail ?? `Falha HTTP ${response.status}`
    logger.error(`Erro HTTP ${response.status} em ${method} ${path}`, {
      requestId: serverRequestId,
      durationMs,
      code: data.error,
      message,
      details: data.details,
    })
    throw new ApiRequestError(message, response.status, data.error, data.request_id ?? serverRequestId, data.details)
  }

  logger.debug(`${method} ${path} -> ${response.status}`, { requestId: serverRequestId, durationMs })
  return (await response.json()) as T
}
