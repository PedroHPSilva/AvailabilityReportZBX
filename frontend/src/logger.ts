// Logger simples para o frontend: além de imprimir no console do navegador
// de forma estruturada, mantém um pequeno histórico em memória para que o
// usuário possa copiar os detalhes de um erro ao pedir ajuda ao suporte.
//
// Cada requisição HTTP (ver api.ts) gera um requestId que é enviado ao
// backend via header "X-Request-Id" e devolvido na resposta / no corpo de
// erro. Esse mesmo ID aparece aqui no console e na tela (ErrorLine), o que
// permite cruzar rapidamente "o que o usuário viu" com "o que está no
// logs/app.log e logs/app-error.log do servidor".

export type LogLevel = "debug" | "info" | "warn" | "error"

export type LogEntry = {
  timestamp: string
  level: LogLevel
  message: string
  context?: Record<string, unknown>
}

const MAX_HISTORY = 50
const history: LogEntry[] = []

function record(level: LogLevel, message: string, context?: Record<string, unknown>) {
  const entry: LogEntry = { timestamp: new Date().toISOString(), level, message, context }
  history.push(entry)
  if (history.length > MAX_HISTORY) history.shift()

  const prefix = `[zabbix-availability] ${entry.timestamp}`
  const consoleArgs: unknown[] = context ? [prefix, message, context] : [prefix, message]
  if (level === "error") console.error(...consoleArgs)
  else if (level === "warn") console.warn(...consoleArgs)
  else if (level === "info") console.info(...consoleArgs)
  else console.debug(...consoleArgs)
}

export const logger = {
  debug: (message: string, context?: Record<string, unknown>) => record("debug", message, context),
  info: (message: string, context?: Record<string, unknown>) => record("info", message, context),
  warn: (message: string, context?: Record<string, unknown>) => record("warn", message, context),
  error: (message: string, context?: Record<string, unknown>) => record("error", message, context),
}

export function getRecentLogs(): LogEntry[] {
  return [...history]
}

export function generateRequestId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID().replace(/-/g, "").slice(0, 12)
  }
  return Math.random().toString(16).slice(2, 14)
}
