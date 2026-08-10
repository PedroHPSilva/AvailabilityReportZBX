import { FormEvent, useEffect, useLayoutEffect, useMemo, useState } from "react"
import {
  calculateGroupTrigger,
  getHostGroups,
  getHosts,
  getSession,
  getTimeline,
  getTriggerGroups,
  login,
  loginWithZabbixSession,
  logout,
  ApiRequestError,
  type AvailabilityResult,
  type AuthSession,
  type GroupTriggerAvailability,
  type Host,
  type HostGroup,
  type Timeline,
  type TriggerGroup,
} from "./api"
import { logger } from "./logger"

// --- Constantes de UI/layout (classes Tailwind reaproveitadas e chaves
// de localStorage) ---
const timezoneDefault = "America/Sao_Paulo"
const fieldClass =
  "h-10 min-w-0 rounded-md border border-slate-300 bg-white px-3 text-sm text-slate-900 outline-none transition focus:border-cyan-700 focus:ring-2 focus:ring-cyan-100 dark:border-slate-600 dark:bg-slate-950 dark:text-slate-100 dark:focus:border-cyan-400 dark:focus:ring-cyan-950"
const labelClass = "grid min-w-0 gap-1 text-[11px] font-semibold uppercase text-slate-500 dark:text-slate-400"
const panelClass =
  "min-w-0 rounded-md border border-slate-200 bg-white p-5 shadow-sm shadow-slate-200/40 dark:border-[#252b36] dark:bg-[#111318] dark:shadow-none"
const themeStorageKey = "zabbix-availability-theme"
const maxHistoryDays = 730
const resultPageSize = 20

const savedFiltersStorageKey = "zabbix-availability-saved-filters"

type SavedFilter = {
  id: string
  name: string
  createdAt: string
  groupIds: string[]
  groups: HostGroup[]
  hostIds: string[]
  hosts: Host[]
  triggerKeys: string[]
  triggers: TriggerGroup[]
}

// Le os filtros salvos do localStorage (por navegador, nao sincroniza
// entre maquinas -- ver ANALISE_E_MELHORIAS.md).
function loadSavedFilters(): SavedFilter[] {
  try {
    const raw = localStorage.getItem(savedFiltersStorageKey)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    // Filtros salvos sao um atalho de conveniencia; se o localStorage estiver
    // indisponivel (modo privado, quota etc.) a aplicacao continua funcional.
    return []
  }
}

// Grava a lista de filtros salvos no localStorage.
function persistSavedFilters(filters: SavedFilter[]) {
  try {
    localStorage.setItem(savedFiltersStorageKey, JSON.stringify(filters))
  } catch {
    // Ignorado de proposito: ver loadSavedFilters().
  }
}

type Theme = "light" | "dark"

// Gera o valor padrão dos campos de período (formato aceito pelo input
// datetime-local, agora com segundos). Para o fim do dia usamos 23:59:59
// (e não 23:59:00), que é como o próprio Zabbix delimita "hoje" nos
// relatórios — ver PERIODO_23_59_59.md para o porquê disso importar no
// cálculo do percentual de disponibilidade.
function localDateTime(offsetDays: number, endOfDay = false) {
  const date = new Date()
  date.setDate(date.getDate() + offsetDays)
  date.setHours(endOfDay ? 23 : 0, endOfDay ? 59 : 0, endOfDay ? 59 : 0, 0)
  const pad = (value: number) => String(value).padStart(2, "0")
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

// Combina duas listas por id, sem duplicar (usado ao aplicar um filtro
// salvo: mescla os grupos/hosts/triggers salvos com o que ja esta
// carregado na tela, sem esperar uma nova chamada a API).
function mergeById<T>(current: T[], incoming: T[], keyOf: (item: T) => string): T[] {
  if (incoming.length === 0) return current
  const byKey = new Map(current.map((item) => [keyOf(item), item]))
  for (const item of incoming) byKey.set(keyOf(item), item)
  return Array.from(byKey.values())
}

// Componente raiz: decide entre tela de login e a aplicacao (GroupHostScreen)
// com base na sessao atual (cookie validado via getSession()).
export function App() {
  const [theme, setTheme] = useState<Theme>(readTheme)
  const [session, setSession] = useState<AuthSession | null>(null)
  const [sessionLoading, setSessionLoading] = useState(true)

  useLayoutEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark")
    document.documentElement.style.colorScheme = theme
    try {
      localStorage.setItem(themeStorageKey, theme)
    } catch {
      // Tema continua funcional na sessao atual se localStorage indisponivel.
    }
  }, [theme])

  useEffect(() => {
    // Quando aberta dentro do Zabbix via o modulo (zabbix-module/), a URL
    // do iframe vem com "?sso=<sessionid da sessao ja logada no Zabbix>"
    // (ver Module.php / views/availability.view.php). Nesse caso, login
    // automatico via /api/auth/sso -- o usuario nunca ve a tela de login.
    // Se o SSO falhar (ex.: sessao do Zabbix expirou), cai de volta para o
    // fluxo normal (getSession() + tela de login).
    async function resolveInitialSession(): Promise<AuthSession> {
      const ssoSessionId = new URLSearchParams(window.location.search).get("sso")
      if (ssoSessionId) {
        // Remove o token da URL imediatamente (antes mesmo do login
        // terminar), para nao ficar visivel/reenviavel num refresh da
        // pagina ou nos favoritos do navegador.
        window.history.replaceState({}, "", window.location.pathname + window.location.hash)
        try {
          return await loginWithZabbixSession(ssoSessionId)
        } catch (error) {
          logger.warn("Login via SSO (modulo Zabbix) falhou, caindo para tela de login normal.", {
            message: error instanceof Error ? error.message : String(error),
          })
        }
      }
      return getSession()
    }

    void resolveInitialSession()
      .then(setSession)
      .catch(() => setSession({ authenticated: false, username: null }))
      .finally(() => setSessionLoading(false))
  }, [])

  async function leaveSession() {
    try {
      await logout()
    } finally {
      setSession({ authenticated: false, username: null })
    }
  }

  return (
    <main className="min-h-screen bg-[#f3f6f8] text-slate-900 dark:bg-[#08090d] dark:text-slate-100">
      <header className="sticky top-0 z-40 border-b border-slate-200 bg-white/95 text-slate-900 backdrop-blur dark:border-[#252b36] dark:bg-[#0b0d11]/95 dark:text-slate-50">
        <div className="mx-auto flex min-h-[72px] max-w-7xl flex-col justify-between gap-4 px-4 py-3 sm:px-6 lg:flex-row lg:items-center">
          <div className="flex min-w-0 items-center gap-3">
            <span aria-hidden="true" className="grid h-9 w-9 shrink-0 place-items-center rounded-md bg-sky-600 text-sm font-bold text-white">Z</span>
            <div className="min-w-0">
              <h1 className="text-lg font-semibold">Zabbix Availability</h1>
              <p className="text-xs text-slate-500 dark:text-slate-400">Disponibilidade por host e trigger</p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {session?.authenticated && (
              <>
                <span className="max-w-48 truncate px-2 text-sm text-slate-500 dark:text-slate-400" title={session.username ?? undefined}>Usuario: <strong className="text-slate-900 dark:text-slate-100">{session.username}</strong></span>
                <button className="h-9 rounded-md border border-rose-200 bg-rose-50 px-4 text-sm font-semibold text-rose-700 transition hover:bg-rose-100 dark:border-rose-900/40 dark:bg-rose-950/40 dark:text-rose-300 dark:hover:bg-rose-950/70" onClick={() => void leaveSession()} type="button">Sair</button>
              </>
            )}
            <ThemeSelector theme={theme} onChange={setTheme} />
          </div>
        </div>
      </header>

      {sessionLoading ? (
        <div className="grid min-h-[calc(100vh-81px)] place-items-center text-sm text-slate-500 dark:text-slate-400">Verificando acesso...</div>
      ) : session?.authenticated ? (
        <GroupHostScreen />
      ) : (
        <LoginScreen onLogin={setSession} />
      )}
    </main>
  )
}

// Formulario de login (usuario/senha do Zabbix).
function LoginScreen({ onLogin }: { onLogin: (session: AuthSession) => void }) {
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")

  async function authenticate(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError("")
    try {
      const authenticatedSession = await login(username.trim(), password)
      setPassword("")
      onLogin(authenticatedSession)
    } catch (caught) {
      setPassword("")
      const message = errorText(caught)
      setError(message === "Usuario ou senha invalidos." ? message : message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="grid min-h-[calc(100vh-81px)] place-items-center px-4 py-10">
      <form className={`${panelClass} grid w-full max-w-sm gap-5`} onSubmit={authenticate}>
        <header>
          <h2 className="text-lg font-semibold text-slate-950 dark:text-white">Acesso</h2>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Entre com seu usuario do Zabbix.</p>
        </header>
        <label className={labelClass}>
          Usuario
          <input autoComplete="username" className={fieldClass} required value={username} onChange={(event) => setUsername(event.target.value)} />
        </label>
        <label className={labelClass}>
          Senha
          <input autoComplete="current-password" className={fieldClass} required type="password" value={password} onChange={(event) => setPassword(event.target.value)} />
        </label>
        {error && <ErrorLine message={error} />}
        <button className="h-10 rounded-md border border-emerald-700 bg-emerald-700 px-5 text-sm font-semibold text-white transition hover:bg-emerald-800 disabled:cursor-wait disabled:opacity-60" disabled={busy} type="submit">
          {busy ? "Entrando..." : "Entrar"}
        </button>
      </form>
    </section>
  )
}

// Tela principal: filtros (grupos/hosts/triggers + periodo), botao de
// calculo e os paineis de resultado. E' o componente mais complexo do
// arquivo -- reune quase todo o estado da aplicacao.
function GroupHostScreen() {
  const [groups, setGroups] = useState<HostGroup[]>([])
  const [hosts, setHosts] = useState<Host[]>([])
  const [groupSearch, setGroupSearch] = useState("")
  const [hostSearch, setHostSearch] = useState("")
  const [triggerSearch, setTriggerSearch] = useState("")
  const [selectedGroupIds, setSelectedGroupIds] = useState<string[]>([])
  const [selectedHostIds, setSelectedHostIds] = useState<string[]>([])
  const [triggerGroups, setTriggerGroups] = useState<TriggerGroup[]>([])
  const [selectedTriggerKeys, setSelectedTriggerKeys] = useState<string[]>([])
  const [periodStart, setPeriodStart] = useState(localDateTime(-7))
  const [periodEnd, setPeriodEnd] = useState(localDateTime(0, true))
  const minPeriodDateTime = useMemo(() => localDateTime(-maxHistoryDays), [])
  const [timezone, setTimezone] = useState(timezoneDefault)
  const [result, setResult] = useState<GroupTriggerAvailability | null>(null)
  const [busy, setBusy] = useState(false)
  const [groupsLoading, setGroupsLoading] = useState(false)
  const [hostsLoading, setHostsLoading] = useState(false)
  const [triggersLoading, setTriggersLoading] = useState(false)
  const [error, setError] = useState("")
  const [groupModalOpen, setGroupModalOpen] = useState(false)
  const [hostModalOpen, setHostModalOpen] = useState(false)
  const [triggerModalOpen, setTriggerModalOpen] = useState(false)
  const [savedFilters, setSavedFilters] = useState<SavedFilter[]>(() => loadSavedFilters())

  useEffect(() => {
    void loadInitialData()
  }, [])

  const selectedGroups = useMemo(() => groups.filter((group) => selectedGroupIds.includes(group.groupid)), [groups, selectedGroupIds])
  const selectedHosts = useMemo(() => hosts.filter((host) => selectedHostIds.includes(host.hostid)), [hosts, selectedHostIds])
  const selectedTriggers = useMemo(() => triggerGroups.filter((trigger) => selectedTriggerKeys.includes(trigger.key)), [triggerGroups, selectedTriggerKeys])

  useEffect(() => {
    if (selectedGroupIds.length > 0) {
      void searchHosts(selectedGroupIds)
    }
  }, [selectedGroupIds])

  async function loadInitialData() {
    setGroupsLoading(true)
    setHostsLoading(true)
    setError("")
    try {
      const [groupData, hostData] = await Promise.all([getHostGroups(""), getHosts("")])
      setGroups(groupData)
      setHosts(hostData)
    } catch (caught) {
      setError(errorText(caught))
    } finally {
      setGroupsLoading(false)
      setHostsLoading(false)
    }
  }

  async function searchGroups() {
    setGroupsLoading(true)
    setError("")
    try {
      setGroups(await getHostGroups(groupSearch))
    } catch (caught) {
      setError(errorText(caught))
    } finally {
      setGroupsLoading(false)
    }
  }

  async function searchHosts(groupIds = selectedGroupIds) {
    setHostsLoading(true)
    setError("")
    try {
      const data = await getHosts(hostSearch, groupIds)
      setHosts(data)
      setSelectedHostIds((current) => current.filter((hostid) => data.some((host) => host.hostid === hostid)))
    } catch (caught) {
      setError(errorText(caught))
    } finally {
      setHostsLoading(false)
    }
  }

  async function loadGroupedTriggers() {
    if (selectedGroupIds.length === 0 && selectedHostIds.length === 0) {
      setError("Selecione ao menos um grupo ou host antes de carregar triggers.")
      return
    }
    setTriggersLoading(true)
    setError("")
    setResult(null)
    try {
      const data = await getTriggerGroups(selectedGroupIds, selectedHostIds, triggerSearch)
      setTriggerGroups(data)
      if (data.length === 0) {
        setError("Nenhuma trigger encontrada para os filtros selecionados.")
      }
    } catch (caught) {
      setError(errorText(caught))
    } finally {
      setTriggersLoading(false)
    }
  }

  async function openTriggerSelection() {
    if (selectedGroupIds.length === 0 && selectedHostIds.length === 0) {
      setError("Selecione ao menos um grupo ou host antes de carregar triggers.")
      return
    }
    setTriggerModalOpen(true)
    await loadGroupedTriggers()
  }

  async function calculate(event: FormEvent) {
    event.preventDefault()
    if (selectedTriggerKeys.length === 0) {
      setError("Selecione ao menos uma trigger antes de calcular.")
      return
    }
    if (new Date(periodStart).getTime() < new Date(minPeriodDateTime).getTime()) {
      setError("O periodo inicial nao pode ser anterior a 730 dias.")
      return
    }
    if (new Date(periodEnd).getTime() <= new Date(periodStart).getTime()) {
      setError("O fim do periodo deve ser posterior ao inicio.")
      return
    }
    setBusy(true)
    setError("")
    setResult(null)
    try {
      const calculated = await calculateGroupTrigger({
          trigger_keys: selectedTriggerKeys,
          period_start: periodStart,
          period_end: periodEnd,
          timezone,
          groupids: selectedGroupIds,
          hostids: selectedHostIds,
        })
      setResult(calculated)
    } catch (caught) {
      setError(errorText(caught))
    } finally {
      setBusy(false)
    }
  }

  function resetFilters() {
    setSelectedGroupIds([])
    setSelectedHostIds([])
    setTriggerGroups([])
    setSelectedTriggerKeys([])
    setResult(null)
    setError("")
  }

  function saveCurrentFilter() {
    if (selectedGroupIds.length === 0 && selectedHostIds.length === 0) {
      setError("Selecione ao menos um grupo ou host antes de salvar o filtro.")
      return
    }
    const suggestedName = [
      selectedGroups.map((group) => group.name).join(", "),
      selectedHosts.map((host) => host.name || host.host).join(", "),
    ]
      .filter(Boolean)
      .join(" / ")
    const name = window.prompt("Nome para o filtro salvo:", suggestedName.slice(0, 60))
    if (!name || !name.trim()) return

    const filter: SavedFilter = {
      id: `${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
      name: name.trim(),
      createdAt: new Date().toISOString(),
      groupIds: selectedGroupIds,
      groups: selectedGroups,
      hostIds: selectedHostIds,
      hosts: selectedHosts,
      triggerKeys: selectedTriggerKeys,
      triggers: selectedTriggers,
    }
    setSavedFilters((current) => {
      const next = [...current.filter((existing) => existing.name !== filter.name), filter]
      persistSavedFilters(next)
      return next
    })
  }

  function applySavedFilter(filter: SavedFilter) {
    // Mescla os grupos/hosts/triggers do filtro salvo com o que ja esta
    // carregado, para que os nomes apareçam de imediato nos PickerBox sem
    // precisar esperar uma nova chamada a API.
    setGroups((current) => mergeById(current, filter.groups, (item) => item.groupid))
    setHosts((current) => mergeById(current, filter.hosts, (item) => item.hostid))
    setTriggerGroups((current) => mergeById(current, filter.triggers, (item) => item.key))
    setSelectedGroupIds(filter.groupIds)
    setSelectedHostIds(filter.hostIds)
    setSelectedTriggerKeys(filter.triggerKeys)
    setResult(null)
    setError("")
  }

  function deleteSavedFilter(id: string) {
    setSavedFilters((current) => {
      const next = current.filter((filter) => filter.id !== id)
      persistSavedFilters(next)
      return next
    })
  }

  return (
    <>
      <div className="mx-auto grid max-w-7xl gap-6 px-4 py-7 sm:px-6">
        <form className={`${panelClass} grid gap-6`} onSubmit={calculate}>
          <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 pb-4 dark:border-[#252b36]">
            <div className="flex items-center gap-2">
              <span aria-hidden="true" className="text-lg text-emerald-600">&#9661;</span>
              <h2 className="text-lg font-semibold text-slate-950 dark:text-white">Filtros</h2>
            </div>
            <SavedFiltersBar
              savedFilters={savedFilters}
              onApply={applySavedFilter}
              onSave={saveCurrentFilter}
              onDelete={deleteSavedFilter}
              canSave={selectedGroupIds.length > 0 || selectedHostIds.length > 0}
            />
          </header>
          <div className="grid gap-6">
            <StepPanel number="1" title="Escolha o escopo">
              <div className="grid gap-3 md:grid-cols-2">
                <PickerBox title="Grupos de hosts" emptyLabel="Selecionar grupos..." hiddenItemsLabel="grupo(s)" items={selectedGroups.map((group) => group.name)} selectionSummary={selectedGroups.length > 0 ? `${selectedGroups.length} grupo(s) selecionado(s)` : undefined} maxVisibleItems={1} onOpen={() => setGroupModalOpen(true)} onClear={() => {
                  setSelectedGroupIds([])
                  setSelectedHostIds([])
                  setTriggerGroups([])
                  setSelectedTriggerKeys([])
                  setResult(null)
                  void searchHosts([])
                }} />
                <PickerBox title="Hosts" emptyLabel="Selecionar hosts..." hiddenItemsLabel="host(s)" items={selectedHosts.map((host) => host.name || host.host)} selectionSummary={selectedHosts.length > 0 ? `${selectedHosts.length} host(s) selecionado(s)` : undefined} maxVisibleItems={1} onOpen={() => setHostModalOpen(true)} onClear={() => {
                  setSelectedHostIds([])
                  setTriggerGroups([])
                  setSelectedTriggerKeys([])
                  setResult(null)
                }} />
              </div>
            </StepPanel>

            <StepPanel number="2" title="Escolha as triggers">
              <PickerBox
                title="Triggers"
                emptyLabel="Selecionar triggers..."
                hiddenItemsLabel="trigger(s)"
                items={selectedTriggers.map(triggerDisplayName)}
                selectionSummary={selectedTriggers.length > 0 ? `${selectedTriggers.length} trigger(s) selecionada(s)` : undefined}
                maxVisibleItems={2}
                onOpen={() => void openTriggerSelection()}
                onClear={() => {
                  setSelectedTriggerKeys([])
                  setResult(null)
                }}
              />
            </StepPanel>

            <StepPanel number="3" title="Periodo">
            <div className="grid gap-3 lg:grid-cols-[minmax(220px,1fr)_minmax(220px,1fr)_auto] lg:items-end">
              <PeriodInputs periodStart={periodStart} periodEnd={periodEnd} timezone={timezone} minDateTime={minPeriodDateTime} onPeriodStart={setPeriodStart} onPeriodEnd={setPeriodEnd} onTimezone={setTimezone} />
            </div>
            <div className="mt-4 flex gap-3">
              <button className="h-11 flex-1 rounded-md border border-emerald-600 bg-emerald-600 px-5 text-sm font-semibold text-white transition hover:bg-emerald-700 disabled:cursor-wait disabled:opacity-60" disabled={busy || triggersLoading || selectedTriggerKeys.length === 0}>
                {busy ? "Processando" : "Calcular disponibilidade"}
              </button>
              <button className="h-11 rounded-md border border-slate-300 px-6 text-sm font-semibold text-slate-700 transition hover:bg-slate-100 dark:border-[#252b36] dark:text-slate-300 dark:hover:bg-[#191c23]" type="button" onClick={resetFilters}>
                Limpar
              </button>
            </div>
            </StepPanel>
          </div>
          {error && <ErrorLine message={error} />}
        </form>

        {busy ? (
          <section className={`${panelClass} grid min-h-40 content-center justify-items-center gap-3 text-center`}>
            <span className="h-8 w-8 animate-spin rounded-full border-4 border-slate-200 border-t-emerald-600 dark:border-slate-700 dark:border-t-emerald-400" />
            <p className="text-sm font-semibold text-slate-700 dark:text-slate-200">Calculando disponibilidade...</p>
            <p className="text-xs text-slate-500 dark:text-slate-400">Aguarde enquanto os eventos da janela selecionada são consultados.</p>
          </section>
        ) : result ? (
          <section className="grid gap-6">
          <AggregateSummary result={result} />
          <CompositionSummary result={result} />
          <GroupComparisonChart result={result} />
          <PerHostResults result={result} />
          </section>
        ) : (
          <p className="py-12 text-center text-sm text-slate-500 dark:text-slate-400">Configure os filtros e clique em "Calcular disponibilidade" para ver os resultados.</p>
        )}
      </div>

      <SelectionModal title="Grupos de hosts" items={groups} idKey="groupid" labelKey="name" selectedIds={selectedGroupIds} open={groupModalOpen} search={groupSearch} onSearchChange={setGroupSearch} onSearch={() => void searchGroups()} onClose={() => setGroupModalOpen(false)} onConfirm={(ids) => {
        setSelectedGroupIds(ids)
        setSelectedHostIds([])
        setTriggerGroups([])
        setSelectedTriggerKeys([])
        setResult(null)
      }} isLoading={groupsLoading} />
      <SelectionModal title="Hosts" items={hosts} idKey="hostid" labelKey="name" selectedIds={selectedHostIds} open={hostModalOpen} search={hostSearch} onSearchChange={setHostSearch} onSearch={() => void searchHosts()} onClose={() => setHostModalOpen(false)} onConfirm={(ids) => {
        setSelectedHostIds(ids)
        setTriggerGroups([])
        setSelectedTriggerKeys([])
        setResult(null)
        setHostModalOpen(false)
      }} isLoading={hostsLoading} />
      <SelectionModal title="Triggers" items={triggerGroups} idKey="key" labelKey="description" getLabel={triggerDisplayName} selectedIds={selectedTriggerKeys} open={triggerModalOpen} search={triggerSearch} onSearchChange={setTriggerSearch} onSearch={() => void loadGroupedTriggers()} onClose={() => setTriggerModalOpen(false)} onConfirm={(ids) => {
        setSelectedTriggerKeys(ids)
        setResult(null)
        setTriggerModalOpen(false)
      }} isLoading={triggersLoading} />
    </>
  )
}

// Barra de "filtros salvos": aplicar/salvar/excluir um filtro (grupos +
// hosts + triggers) persistido no localStorage.
function SavedFiltersBar({
  savedFilters,
  onApply,
  onSave,
  onDelete,
  canSave,
}: {
  savedFilters: SavedFilter[]
  onApply: (filter: SavedFilter) => void
  onSave: () => void
  onDelete: (id: string) => void
  canSave: boolean
}) {
  const [selectedId, setSelectedId] = useState("")
  const selectedFilter = savedFilters.find((filter) => filter.id === selectedId)

  return (
    <div className="flex flex-wrap items-center gap-2">
      {savedFilters.length > 0 && (
        <>
          <select
            className={`${fieldClass} h-9 max-w-56`}
            value={selectedId}
            onChange={(event) => setSelectedId(event.target.value)}
            aria-label="Filtros salvos"
          >
            <option value="">Filtros salvos...</option>
            {savedFilters.map((filter) => (
              <option key={filter.id} value={filter.id}>
                {filter.name}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="h-9 rounded-md border border-cyan-700 bg-cyan-700 px-3 text-xs font-semibold text-white transition hover:bg-cyan-800 disabled:cursor-not-allowed disabled:opacity-50"
            disabled={!selectedFilter}
            onClick={() => selectedFilter && onApply(selectedFilter)}
          >
            Aplicar
          </button>
          <button
            type="button"
            className="h-9 rounded-md border border-rose-300 px-3 text-xs font-semibold text-rose-700 transition hover:bg-rose-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-rose-900/50 dark:text-rose-300 dark:hover:bg-rose-950/40"
            disabled={!selectedFilter}
            onClick={() => {
              if (!selectedFilter) return
              onDelete(selectedFilter.id)
              setSelectedId("")
            }}
          >
            Excluir
          </button>
        </>
      )}
      <button
        type="button"
        className="h-9 rounded-md border border-slate-300 px-3 text-xs font-semibold text-slate-700 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-[#252b36] dark:text-slate-300 dark:hover:bg-[#191c23]"
        disabled={!canSave}
        title={canSave ? undefined : "Selecione ao menos um grupo ou host para salvar"}
        onClick={onSave}
      >
        Salvar filtro atual
      </button>
    </div>
  )
}

type PeriodInputsProps = {
  periodStart: string
  periodEnd: string
  timezone: string
  minDateTime: string
  onPeriodStart: (value: string) => void
  onPeriodEnd: (value: string) => void
  onTimezone: (value: string) => void
}

// Campos de periodo (inicio/fim) + fuso horario.
function PeriodInputs({ periodStart, periodEnd, timezone, minDateTime, onPeriodStart, onPeriodEnd, onTimezone }: PeriodInputsProps) {
  // step={1} habilita o seletor de segundos no input datetime-local; sem
  // isso o navegador trunca qualquer valor para o minuto (ex.: 23:59:59
  // viraria 23:59:00 na hora de exibir/editar), o que é exatamente o bug
  // que este ajuste corrige (ver PERIODO_23_59_59.md).
  return (
    <>
      <label className={labelClass}>Inicio<input className={fieldClass} min={minDateTime} step={1} type="datetime-local" value={periodStart} onChange={(event) => onPeriodStart(event.target.value)} /></label>
      <label className={labelClass}>Fim<input className={fieldClass} min={minDateTime} step={1} type="datetime-local" value={periodEnd} onChange={(event) => onPeriodEnd(event.target.value)} /></label>
      <details className="col-span-full mt-1">
        <summary className="cursor-pointer text-sm font-semibold text-emerald-600 dark:text-emerald-400">Configuração avançada</summary>
        <div className="mt-2 grid gap-2">
          <label className={labelClass}>Fuso horario<input className={fieldClass} value={timezone} onChange={(event) => onTimezone(event.target.value)} /></label>
        </div>
      </details>
    </>
  )
}

// Envelope visual usado para numerar as etapas da tela ("1", "2", "3"...).
function StepPanel({ number, title, children }: { number: string; title: string; children: React.ReactNode }) {
  return (
    <section>
      <header className="mb-3 flex items-center gap-2">
        <span className="grid h-6 w-6 place-items-center rounded-full bg-emerald-500 text-xs font-semibold text-white dark:text-[#08090d]">{number}</span>
        <h2 className="text-sm font-semibold text-slate-950 dark:text-white">{title}</h2>
      </header>
      {children}
    </section>
  )
}

// Campo "somente leitura" que mostra a selecao atual (grupos/hosts/
// triggers) e abre o SelectionModal correspondente ao ser clicado.
function PickerBox({ title, emptyLabel, hiddenItemsLabel, items, selectionSummary, maxVisibleItems, onOpen, onClear }: { title: string; emptyLabel: string; hiddenItemsLabel: string; items: string[]; selectionSummary?: string; maxVisibleItems?: number; onOpen: () => void; onClear: () => void }) {
  const visibleItems = maxVisibleItems ? items.slice(0, maxVisibleItems) : items
  const hiddenItemCount = items.length - visibleItems.length

  return (
    <div className="grid min-w-0 content-start gap-2 text-sm font-semibold text-slate-950 dark:text-white">
      <span>{title}</span>
      <button className="h-[78px] w-full min-w-0 overflow-hidden rounded-md border border-slate-300 bg-white px-4 py-2 text-left text-sm font-normal transition hover:border-emerald-500 dark:border-[#252b36] dark:bg-[#08090d] dark:hover:border-emerald-500" type="button" onClick={onOpen}>
        {items.length === 0 ? <span className="text-slate-500 dark:text-slate-400">{emptyLabel}</span> : (
          <span className="grid gap-1">
            {selectionSummary && <span className="font-medium text-slate-800 dark:text-slate-100">{selectionSummary}</span>}
            {visibleItems.map((item) => (
              <span key={item} className="block truncate text-xs text-slate-500 dark:text-slate-400" title={item}>{item}</span>
            ))}
            {hiddenItemCount > 0 && <span className="text-xs text-slate-500 dark:text-slate-400">+ {hiddenItemCount} outro(s) {hiddenItemsLabel}</span>}
          </span>
        )}
      </button>
      {items.length > 0 && <button className="w-fit text-xs font-semibold text-slate-500 hover:text-slate-900 dark:text-slate-400 dark:hover:text-white" type="button" onClick={onClear}>Limpar seleção</button>}
    </div>
  )
}

// Nome amigavel de um TriggerGroup para exibir nos PickerBox/paineis.
function triggerDisplayName(trigger: TriggerGroup) {
  const hostName = trigger.hosts[0]?.name || trigger.hosts[0]?.host || "Host nao identificado"
  return `${hostName} | ${trigger.description}`
}

// Modal generico de selecao multipla com busca, reaproveitado para
// grupos, hosts e triggers (o tipo T varia conforme o uso).
function SelectionModal<T>({ title, items, idKey, labelKey, getLabel, selectedIds, open, search, onSearchChange, onSearch, onClose, onConfirm, isLoading = false }: { title: string; items: T[]; idKey: keyof T; labelKey: keyof T; getLabel?: (item: T) => string; selectedIds: string[]; open: boolean; search: string; onSearchChange: (value: string) => void; onSearch: () => void; onClose: () => void; onConfirm: (ids: string[]) => void; isLoading?: boolean }) {
  const [draftIds, setDraftIds] = useState<string[]>(selectedIds)
  useEffect(() => {
    if (open) setDraftIds(selectedIds)
  }, [open, selectedIds])

  useEffect(() => {
    if (!open) return
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") onClose()
    }
    window.addEventListener("keydown", closeOnEscape)
    return () => window.removeEventListener("keydown", closeOnEscape)
  }, [open, onClose])

  if (!open) return null

  function toggle(id: string) {
    setDraftIds((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]))
  }

  function confirmSelection() {
    const confirmedIds = [...draftIds]
    onClose()
    onConfirm(confirmedIds)
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/55 px-4" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose()
    }}>
      <section className="flex max-h-[84vh] min-h-0 w-full max-w-3xl flex-col overflow-hidden rounded-md border border-slate-700 bg-white text-slate-900 shadow-xl dark:bg-[#262626] dark:text-slate-100" onMouseDown={(event) => event.stopPropagation()}>
        <header className="flex items-center justify-between border-b border-slate-200 px-4 py-3 dark:border-slate-700">
          <h2 className="text-base font-semibold">{title}</h2>
          <button className="text-xl text-slate-500 hover:text-slate-900 dark:text-slate-400 dark:hover:text-white" type="button" onClick={(event) => {
            event.preventDefault()
            event.stopPropagation()
            onClose()
          }}>x</button>
        </header>
        <form className="grid gap-3 border-b border-slate-200 p-3 dark:border-slate-700 sm:grid-cols-[minmax(0,1fr)_auto]" onSubmit={(event) => {
          event.preventDefault()
          event.stopPropagation()
          if (!isLoading) onSearch()
        }}>
          <input className={fieldClass} disabled={isLoading} placeholder="type here to search" value={search} onChange={(event) => onSearchChange(event.target.value)} />
          <button className="h-10 rounded-md border border-slate-700 bg-slate-700 px-4 text-sm font-semibold text-white disabled:cursor-wait disabled:opacity-60" disabled={isLoading} type="button" onClick={(event) => {
            event.preventDefault()
            event.stopPropagation()
            onSearch()
          }}>{isLoading ? "Buscando" : "Buscar"}</button>
        </form>
        <div className="border-b border-slate-200 px-3 py-2 text-xs text-slate-500 dark:border-slate-700 dark:text-slate-400">
          {isLoading ? (
            <span>Carregando lista...</span>
          ) : (
            <span>{items.length} item(ns) carregado(s).</span>
          )}
        </div>
        <div className="min-h-0 flex-1 overflow-auto p-3">
          <label className="mb-2 grid grid-cols-[28px_1fr] items-center gap-2 border-b border-slate-200 pb-2 text-xs font-semibold uppercase text-slate-500 dark:border-slate-700 dark:text-slate-400">
            <input type="checkbox" disabled={isLoading || items.length === 0} checked={items.length > 0 && items.every((item) => draftIds.includes(String(item[idKey])))} onChange={(event) => {
              const itemIds = items.map((item) => String(item[idKey]))
              setDraftIds((current) => event.target.checked ? Array.from(new Set([...current, ...itemIds])) : current.filter((id) => !itemIds.includes(id)))
            }} />
            Name
          </label>
          <div className="grid">
            {!isLoading && items.length === 0 && (
              <div className="py-8 text-center text-sm text-slate-500 dark:text-slate-400">Nenhum item encontrado.</div>
            )}
            {isLoading && (
              <div className="py-8 text-center text-sm text-slate-500 dark:text-slate-400">Carregando...</div>
            )}
            {!isLoading && items.map((item) => {
              const id = String(item[idKey])
              return (
                <label key={id} className="grid min-h-8 grid-cols-[28px_1fr] items-center gap-2 border-b border-slate-100 px-1 text-sm hover:bg-slate-100 dark:border-slate-700 dark:hover:bg-[#333]">
                  <input type="checkbox" checked={draftIds.includes(id)} onChange={() => toggle(id)} />
                  <span className="text-sky-700 dark:text-sky-400">{getLabel ? getLabel(item) : String(item[labelKey])}</span>
                </label>
              )
            })}
          </div>
        </div>
        <footer className="shrink-0 flex justify-end gap-2 border-t border-slate-200 px-4 py-3 dark:border-slate-700">
          <button className="rounded-md border border-sky-700 bg-sky-700 px-4 py-2 text-sm font-semibold text-white disabled:cursor-wait disabled:opacity-60" disabled={isLoading} type="button" onClick={(event) => {
            event.preventDefault()
            event.stopPropagation()
            confirmSelection()
          }}>
            Selecionar
          </button>
          <button className="rounded-md border border-slate-400 px-4 py-2 text-sm text-slate-700 dark:border-slate-600 dark:text-slate-200" type="button" onClick={(event) => {
            event.preventDefault()
            event.stopPropagation()
            onClose()
          }}>Cancelar</button>
        </footer>
      </section>
    </div>
  )
}

// Alternador de tema claro/escuro (persistido em localStorage).
function ThemeSelector({ theme, onChange }: { theme: Theme; onChange: (theme: Theme) => void }) {
  return (
    <div aria-label="Tema" className="grid w-fit grid-cols-2 rounded-md border border-slate-200 bg-slate-100 p-1 text-xs font-semibold dark:border-[#252b36] dark:bg-[#111318]" role="group">
      <button aria-pressed={theme === "light"} className={`h-8 min-w-14 rounded-sm px-2 transition ${theme === "light" ? "bg-white text-slate-950 shadow-sm" : "text-slate-500 hover:text-slate-900 dark:text-slate-400"}`} onClick={() => onChange("light")} type="button">Claro</button>
      <button aria-pressed={theme === "dark"} className={`h-8 min-w-14 rounded-sm px-2 transition ${theme === "dark" ? "bg-[#252b36] text-slate-50" : "text-slate-500 hover:text-slate-900"}`} onClick={() => onChange("dark")} type="button">Escuro</button>
    </div>
  )
}

// Cartao de resumo agregado (media/melhor/pior percentual do grupo).
function AggregateSummary({ result }: { result: GroupTriggerAvailability | null }) {
  if (!result) return null
  const maxProblemPercent = result.results.reduce((maxValue, row) => Math.max(maxValue, row.problem_percent ?? 0), 0)
  return (
    <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-7">
      <MetricGrid items={[
        ["Disponibilidade", percent(result.average_availability_percent), "ok"],
        ["Quantidade de hosts", String(result.host_count), "info"],
        ["Itens calculados", String(result.calculated_count), "info"],
        ["Incidentes", String(result.total_incident_count), "warning"],
        ["Maior indisponibilidade", percent(maxProblemPercent), "problem"],
        ["Menor disponibilidade", percent(result.worst_availability_percent), "warning"],
        ["Maior disponibilidade", percent(result.best_availability_percent), "ok"],
      ]} />
    </section>
  )
}

// Mostra quantos hosts ficaram OK/parcial/inconclusivo dentro do grupo.
function CompositionSummary({ result }: { result: GroupTriggerAvailability }) {
  const availability = result.average_availability_percent ?? 0
  const problem = Math.max(0, 100 - availability)
  return (
    <section className={panelClass}>
      <h2 className="mb-4 text-sm font-semibold text-slate-950 dark:text-white">Composicao de disponibilidade</h2>
      <StackedAvailabilityBar availability={availability} problem={problem} />
      <div className="mt-4 flex gap-5 text-xs text-slate-500 dark:text-slate-400">
        <span className="flex items-center gap-2"><i className="h-3 w-3 rounded-full bg-emerald-500" />Disponibilidade</span>
        <span className="flex items-center gap-2"><i className="h-3 w-3 rounded-full bg-rose-500" />Indisponibilidade</span>
      </div>
    </section>
  )
}

// Grafico de barras comparando a disponibilidade entre os hosts do grupo.
function GroupComparisonChart({ result }: { result: GroupTriggerAvailability | null }) {
  const rankedResults = result?.results
    .filter((row) => row.availability_percent !== null && row.problem_percent !== null)
    .sort((left, right) => (left.availability_percent ?? 0) - (right.availability_percent ?? 0))
    .slice(0, 12) ?? []

  return (
    <section className={panelClass}>
      <h2 className="mb-4 text-sm font-semibold text-slate-950 dark:text-white">Menores disponibilidades por item</h2>
      {rankedResults.length === 0 ? (
        <div className="grid min-h-36 content-center text-sm text-slate-500 dark:text-slate-400">Sem dados calculados.</div>
      ) : (
        <div className="grid gap-3">
          {rankedResults.map((row) => (
            <div className="flex min-w-0 items-center justify-between gap-3 rounded-md bg-slate-50 px-3 py-3 dark:bg-[#08090d]" key={row.triggerid}>
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-slate-900 dark:text-slate-100">{row.trigger_name}</p>
                <p className="truncate text-xs text-slate-500 dark:text-slate-400">{row.hosts[0]?.name ?? "Host nao identificado"}</p>
              </div>
              <strong className="shrink-0 tabular-nums text-emerald-700 dark:text-emerald-400">{percent(row.availability_percent)}</strong>
            </div>
          ))}
          {result && result.results.length > rankedResults.length && (
            <span className="text-xs text-slate-500 dark:text-slate-400">Exibindo os 12 itens com menor disponibilidade.</span>
          )}
        </div>
      )}
    </section>
  )
}

// Barra unica OK/PROBLEM (proporcional aos segundos de cada estado).
function StackedAvailabilityBar({ availability, problem, compact = false }: { availability: number; problem: number; compact?: boolean }) {
  return (
    <div className={`flex w-full overflow-hidden rounded-full bg-slate-100 dark:bg-[#252b36] ${compact ? "h-3" : "h-8"}`}>
      {availability > 0 && <span className="grid place-items-center bg-emerald-500 text-xs font-semibold text-emerald-950" style={{ width: `${availability}%` }} title={`Disponibilidade: ${percent(availability)}`}>{!compact && availability >= 12 ? percent(availability) : ""}</span>}
      {problem > 0 && <span className="grid place-items-center bg-rose-500 text-xs font-semibold text-white" style={{ width: `${problem}%` }} title={`Indisponibilidade: ${percent(problem)}`}>{!compact && problem >= 12 ? percent(problem) : ""}</span>}
    </div>
  )
}

// Tabela com o resultado individual de cada host/trigger do grupo,
// com paginacao (resultPageSize) e exportacao (CSV/PDF).
function PerHostResults({ result }: { result: GroupTriggerAvailability | null }) {
  const [page, setPage] = useState(1)
  const rows = result?.results ?? []
  const pageCount = Math.max(1, Math.ceil(rows.length / resultPageSize))
  const currentPage = Math.min(page, pageCount)
  const pageStartIndex = (currentPage - 1) * resultPageSize
  const pageRows = rows.slice(pageStartIndex, pageStartIndex + resultPageSize)
  const firstVisibleRow = rows.length > 0 ? pageStartIndex + 1 : 0
  const lastVisibleRow = Math.min(pageStartIndex + pageRows.length, rows.length)

  useEffect(() => {
    setPage(1)
  }, [result])

  return (
    <section className={panelClass}>
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-200 pb-4 dark:border-[#252b36]">
        <div>
          <h2 className="text-sm font-semibold text-slate-950 dark:text-white">Resultado por host</h2>
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            {result ? `${rows.length} linha(s) - exibindo ${firstVisibleRow} a ${lastVisibleRow}` : "Aguardando calculo"}
          </p>
        </div>
        {result && rows.length > 0 && (
          <div className="flex flex-wrap gap-2">
            <button
              className="h-9 rounded-md border border-emerald-700 bg-emerald-700 px-4 text-sm font-semibold text-white transition hover:bg-emerald-800"
              onClick={() => exportResultCsv(result)}
              type="button"
            >
              Exportar CSV
            </button>
            <button
              className="h-9 rounded-md border border-sky-700 bg-sky-700 px-4 text-sm font-semibold text-white transition hover:bg-sky-800"
              onClick={() => exportResultPdf(result)}
              type="button"
            >
              Exportar PDF
            </button>
          </div>
        )}
      </header>
      {!result ? (
        <div className="grid min-h-80 content-center text-sm text-slate-500 dark:text-slate-400">Selecione o escopo, escolha uma trigger e calcule.</div>
      ) : (
        <div className="mt-3 overflow-auto">
          <table className="w-full min-w-[1100px] border-collapse text-left text-sm">
            <thead className="text-[11px] uppercase text-slate-500 dark:text-slate-400">
              <tr className="border-b border-slate-200 dark:border-[#252b36]">
                <th className="py-2 pr-3">Host</th>
                <th className="py-2 pr-3">Item monitorado</th>
                <th className="py-2 pr-3">Indisponibilidade</th>
                <th className="py-2 pr-3">Disponibilidade</th>
                <th className="py-2 pr-3">Tempo indisponivel</th>
                <th className="py-2 pr-3">Incidentes</th>
                <th className="py-2 text-center">Auditoria</th>
              </tr>
            </thead>
            <tbody>
              {pageRows.map((row) => <AvailabilityResultRow key={row.triggerid} row={row} />)}
            </tbody>
          </table>
          {pageCount > 1 && (
            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 pt-3 text-sm dark:border-[#252b36]">
              <span className="text-slate-500 dark:text-slate-400">Página {currentPage} de {pageCount}</span>
              <div className="flex gap-2">
                <button
                  className="h-9 rounded-md border border-slate-300 px-3 font-semibold text-slate-700 disabled:cursor-not-allowed disabled:opacity-50 dark:border-[#39414d] dark:text-slate-200"
                  disabled={currentPage <= 1}
                  onClick={() => setPage((value) => Math.max(1, value - 1))}
                  type="button"
                >
                  Anterior
                </button>
                <button
                  className="h-9 rounded-md border border-slate-300 px-3 font-semibold text-slate-700 disabled:cursor-not-allowed disabled:opacity-50 dark:border-[#39414d] dark:text-slate-200"
                  disabled={currentPage >= pageCount}
                  onClick={() => setPage((value) => Math.min(pageCount, value + 1))}
                  type="button"
                >
                  Próxima
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  )
}

// Grade de metricas (label + valor + cor) reaproveitada em varios paineis.
function MetricGrid({ items }: { items: Array<[string, string, "ok" | "problem" | "warning" | "info"]> }) {
  return <>{items.map(([label, value, tone]) => <Metric key={label} label={label} value={value} tone={tone} />)}</>
}

function Metric({ label, value, tone }: { label: string; value: string; tone: "ok" | "problem" | "warning" | "info" }) {
  const toneClass = tone === "ok"
    ? "text-emerald-700 dark:text-emerald-400"
    : tone === "problem"
      ? "text-rose-600 dark:text-rose-400"
      : tone === "warning"
        ? "text-amber-600 dark:text-amber-400"
        : "text-sky-700 dark:text-sky-400"
  return (
    <article className="grid min-h-24 content-between gap-2 rounded-md border border-slate-200 bg-white p-4 dark:border-[#252b36] dark:bg-[#111318]">
      <span className="text-xs text-slate-500 dark:text-slate-400">{label}</span>
      <strong className={`break-words text-2xl font-semibold tabular-nums ${toneClass}`}>{value}</strong>
    </article>
  )
}

// Linha de erro exibida na tela (mensagem ja formatada por errorText()).
function ErrorLine({ message }: { message: string }) {
  return <p className="border-l-4 border-rose-600 bg-rose-50 px-3 py-2 text-sm text-rose-900 dark:bg-rose-950/60 dark:text-rose-100">{message}</p>
}

// Formata segundos como "XhYmZs" para exibicao.
function duration(seconds: number) {
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const remainder = seconds % 60
  return `${hours}h ${minutes}m ${remainder}s`
}

// Formata um percentual (ou "-" quando nao calculavel).
function percent(value: number | null) {
  return value === null ? "-" : `${value.toFixed(4)}%`
}

// --- Exportacao de resultados: CSV (simples) e PDF (gerado "na mao",
// sem biblioteca -- ver ANALISE_E_MELHORIAS.md sobre migrar para jspdf) ---
function exportResultCsv(result: GroupTriggerAvailability) {
  const headers = [
    "Host",
    "Item monitorado",
    "Disponibilidade (%)",
    "Indisponibilidade (%)",
    "Tempo indisponivel",
    "Incidentes",
    "Periodo inicial",
    "Periodo final",
    "Trigger ID",
    "Host ID",
  ]
  const rows = result.results.map((row) => [
    row.hosts[0]?.name ?? "nao identificado",
    row.trigger_name,
    csvPercent(row.availability_percent),
    csvPercent(row.problem_percent),
    duration(row.problem_seconds),
    String(row.incident_count),
    clock(row.period_start),
    clock(row.period_end),
    row.triggerid,
    row.hosts[0]?.hostid ?? "",
  ])
  const csv = [headers, ...rows].map((row) => row.map(csvCell).join(";")).join("\r\n")
  const blob = new Blob([`\uFEFF${csv}`], { type: "text/csv;charset=utf-8" })
  const url = URL.createObjectURL(blob)
  const link = document.createElement("a")
  const dateSuffix = new Date().toISOString().slice(0, 10)
  link.href = url
  link.download = `zabbix_disponibilidade_${dateSuffix}.csv`
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

// Ponto de entrada da exportacao em PDF (delega para buildAvailabilityPdf*).
function exportResultPdf(result: GroupTriggerAvailability) {
  const pdf = buildAvailabilityPdfPerTrigger(result)
  const blob = new Blob([pdf], { type: "application/pdf" })
  const url = URL.createObjectURL(blob)
  const link = document.createElement("a")
  link.href = url
  link.download = `zabbix_disponibilidade_${new Date().toISOString().slice(0, 10)}.pdf`
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

function buildAvailabilityPdfPerTrigger(result: GroupTriggerAvailability) {
  const availability = percent(result.average_availability_percent)
  const availabilityValue = result.average_availability_percent ?? 0
  const problemValue = Math.max(0, 100 - availabilityValue)
  const problem = percent(result.average_availability_percent === null ? null : problemValue)
  const periodStart = result.results[0]?.period_start ? clock(result.results[0].period_start) : "-"
  const periodEnd = result.results[0]?.period_end ? clock(result.results[0].period_end) : "-"
  const generatedAt = new Date().toLocaleString("pt-BR")
  const periodSeconds = result.results[0]?.total_seconds ?? 0
  const problemSeconds = result.results.reduce((sum, row) => sum + row.problem_seconds, 0)
  const stablePeriod = result.total_incident_count === 0 && problemSeconds === 0
  const resultMessage = stablePeriod
    ? "Nenhum incidente registrado e nenhuma indisponibilidade apurada no intervalo analisado."
    : `${result.total_incident_count} incidente(s) registrado(s) no intervalo analisado.`
  const resultBadge = stablePeriod ? "OPERAÇÃO ESTÁVEL" : "ATENÇÃO"
  const resultBadgeColor: [number, number, number] = stablePeriod ? [0.04, 0.60, 0.42] : [0.78, 0.39, 0.02]
  const worstRows = [...result.results]
    .filter((row) => row.availability_percent !== null)
    .sort((left, right) => (left.availability_percent ?? 0) - (right.availability_percent ?? 0))
    .slice(0, 8)
    .map((row) => [
      row.hosts[0]?.name ?? row.hosts[0]?.host ?? "não identificado",
      row.trigger_name,
      percent(row.availability_percent),
      percent(row.problem_percent),
    ])

  const totalPages = Math.max(1, result.results.length + 1)
  const pages: string[] = []
  const summary: string[] = []

  addPdfHeader(summary, "Relatório de Disponibilidade", "Resumo executivo - consulta por host e trigger", generatedAt)
  addResultBanner(summary, "Resultado do período", resultMessage, resultBadge, resultBadgeColor, 42, 725)
  addPdfInfo(summary, [
    ["Consulta", "Disponibilidade por host e trigger"],
    ["Período inicial", periodStart],
    ["Corte de tempo", "24x7"],
    ["Período final", periodEnd],
    ["Duração", duration(periodSeconds)],
    ["Hosts avaliados", String(result.host_count)],
    ["Itens calculados", String(result.calculated_count)],
  ], 42, 628)
  addPdfCard(summary, 42, 548, 118, "Disponibilidade média", availability, [0.03, 0.47, 0.34])
  addPdfCard(summary, 172, 548, 118, "Indisponibilidade", problem, [0.75, 0.07, 0.24])
  addPdfCard(summary, 302, 548, 118, "Incidentes", String(result.total_incident_count), [0.78, 0.39, 0.02])
  addPdfCard(summary, 432, 548, 118, "Maior indisponibilidade", duration(result.max_problem_seconds), [0.75, 0.07, 0.24])

  addText(summary, 42, 490, "Itens com menor disponibilidade", 12, true)
  addText(summary, 42, 474, stablePeriod ? "Todos os itens do recorte estão em 100% de disponibilidade." : "Itens com menor disponibilidade no recorte analisado.", 8, false, [0.33, 0.39, 0.47])
  addPdfWorstTable(summary, worstRows, 42, 452)
  addPdfFooter(summary, generatedAt, 1, totalPages)
  pages.push(summary.join("\n"))

  result.results.forEach((row, index) => {
    const page: string[] = []
    addPdfTriggerCompositionPage(page, row, generatedAt)
    addPdfFooter(page, generatedAt, index + 2, totalPages)
    pages.push(page.join("\n"))
  })

  return encodePdf(pages)
}

function addPdfTriggerCompositionPage(commands: string[], row: AvailabilityResult, generatedAt: string) {
  const host = row.hosts[0]?.name ?? row.hosts[0]?.host ?? "não identificado"
  const availabilityValue = row.availability_percent ?? 0
  const problemValue = row.problem_percent ?? Math.max(0, 100 - availabilityValue)
  const rowStable = row.incident_count === 0 && row.problem_seconds === 0
  const resultMessage = rowStable
    ? "Nenhum incidente registrado para esta trigger no intervalo analisado."
    : `${row.incident_count} incidente(s) registrado(s) para esta trigger.`
  const resultBadge = rowStable ? "OPERAÇÃO ESTÁVEL" : "ATENÇÃO"
  const resultBadgeColor: [number, number, number] = rowStable ? [0.04, 0.60, 0.42] : [0.78, 0.39, 0.02]

  addPdfHeader(commands, "Disponibilidade por Trigger", truncate(host, 72), generatedAt)
  addResultBanner(commands, "Resultado da trigger", resultMessage, resultBadge, resultBadgeColor, 42, 725)
  addPdfInfo(commands, [
    ["Host", host],
    ["Trigger ID", row.triggerid],
    ["Trigger", row.trigger_name],
    ["Corte de tempo", "24x7"],
    ["Período inicial", clock(row.period_start)],
    ["Período final", clock(row.period_end)],
    ["Duração", duration(row.total_seconds)],
    ["Incidentes", String(row.incident_count)],
  ], 42, 628)
  addPdfCard(commands, 42, 548, 118, "Disponibilidade", percent(row.availability_percent), [0.03, 0.47, 0.34])
  addPdfCard(commands, 172, 548, 118, "Indisponibilidade", percent(row.problem_percent), [0.75, 0.07, 0.24])
  addPdfCard(commands, 302, 548, 118, "Tempo indisponível", duration(row.problem_seconds), [0.75, 0.07, 0.24])
  addPdfCard(commands, 432, 548, 118, "Maior indisponibilidade", duration(row.max_problem_seconds), [0.75, 0.07, 0.24])

  addText(commands, 42, 510, "Composição do período", 12, true)
  addPdfDonut(commands, 155, 400, 72, 36, availabilityValue)
  addRect(commands, 104, 306, 8, 8, true, [0.03, 0.47, 0.34])
  addText(commands, 118, 307, "Disponível", 7, true, [0.03, 0.47, 0.34])
  addRect(commands, 196, 306, 8, 8, true, [0.75, 0.07, 0.24])
  addText(commands, 210, 307, "Indisponível", 7, true, [0.75, 0.07, 0.24])

  addText(commands, 318, 455, "Tempo disponível", 8, true)
  addText(commands, 438, 455, duration(row.ok_seconds), 8)
  addText(commands, 318, 430, "Tempo indisponível", 8, true)
  addText(commands, 438, 430, duration(row.problem_seconds), 8)
  addText(commands, 318, 405, "Disponibilidade", 8, true)
  addText(commands, 438, 405, percent(row.availability_percent), 8, false, [0.03, 0.47, 0.34])
  addText(commands, 318, 380, "Indisponibilidade", 8, true)
  addText(commands, 438, 380, percent(row.problem_percent), 8, false, [0.75, 0.07, 0.24])
  addPdfBar(commands, 318, 350, 220, 14, availabilityValue)

  addText(commands, 42, 245, "Resumo da trigger", 12, true)
  addPdfGenericTable(
    commands,
    [[host, row.trigger_name, percent(row.availability_percent), percent(row.problem_percent), duration(row.problem_seconds), String(row.incident_count)]],
    ["Host", "Trigger", "Disp.", "Indisp.", "Tempo indisponível", "Incidentes"],
    [96, 190, 58, 58, 90, 54],
    42,
    220,
    20,
    7,
    ["default", "default", "availability", "problem", "default", "default"],
  )
  addText(commands, 42, 178, `Status do cálculo: ${row.calculation_status}. Manutenção não considerada.`, 8, false, [0.33, 0.39, 0.47])
  addText(commands, 42, 164, `Estado inicial: ${initialStateLabel(row.initial_state)}.`, 8, false, [0.33, 0.39, 0.47])
  addText(commands, 42, 150, `Indisponibilidade apurada: ${problemValue.toFixed(4)}%.`, 8, false, [0.33, 0.39, 0.47])
}

function buildAvailabilityPdfV2(result: GroupTriggerAvailability) {
  const availability = percent(result.average_availability_percent)
  const availabilityValue = result.average_availability_percent ?? 0
  const problemValue = Math.max(0, 100 - availabilityValue)
  const problem = percent(result.average_availability_percent === null ? null : problemValue)
  const worst = percent(result.worst_availability_percent)
  const best = percent(result.best_availability_percent)
  const periodStart = result.results[0]?.period_start ? clock(result.results[0].period_start) : "-"
  const periodEnd = result.results[0]?.period_end ? clock(result.results[0].period_end) : "-"
  const generatedAt = new Date().toLocaleString("pt-BR")
  const totalSeconds = result.results.reduce((sum, row) => sum + row.total_seconds, 0)
  const problemSeconds = result.results.reduce((sum, row) => sum + row.problem_seconds, 0)
  const okSeconds = Math.max(0, totalSeconds - problemSeconds)
  const stablePeriod = result.total_incident_count === 0 && problemSeconds === 0
  const resultMessage = stablePeriod
    ? "Nenhum incidente registrado e nenhuma indisponibilidade apurada no intervalo analisado."
    : `${result.total_incident_count} incidente(s) registrado(s) no intervalo analisado.`
  const resultBadge = stablePeriod ? "OPERAÇÃO ESTÁVEL" : "ATENÇÃO"
  const resultBadgeColor: [number, number, number] = stablePeriod ? [0.04, 0.60, 0.42] : [0.78, 0.39, 0.02]
  const hostBase = uniqueText(result.results.map((row) => row.hosts[0]?.name ?? row.hosts[0]?.host ?? "não identificado"))
  const detailRows = result.results.map((row) => [
    row.hosts[0]?.name ?? row.hosts[0]?.host ?? "não identificado",
    row.trigger_name,
    percent(row.problem_percent),
    percent(row.availability_percent),
    duration(row.problem_seconds),
    String(row.incident_count),
  ])
  const worstRows = [...result.results]
    .filter((row) => row.availability_percent !== null)
    .sort((left, right) => (left.availability_percent ?? 0) - (right.availability_percent ?? 0))
    .slice(0, 5)
    .map((row) => [
      row.hosts[0]?.name ?? row.hosts[0]?.host ?? "não identificado",
      row.trigger_name,
      percent(row.availability_percent),
      percent(row.problem_percent),
    ])

  const detailChunks: string[][][] = []
  for (let index = 0; index < detailRows.length; index += 31) {
    detailChunks.push(detailRows.slice(index, index + 31))
  }
  const totalPages = Math.max(1, detailChunks.length + 1)
  const pages: string[] = []

  const summary: string[] = []
  addPdfHeader(summary, "Relatório de Disponibilidade", "Resumo executivo - consulta por host e trigger", generatedAt)
  addResultBanner(summary, "Resultado do período", resultMessage, resultBadge, resultBadgeColor, 42, 725)
  addPdfInfo(summary, [
    ["Consulta", "Disponibilidade por host e trigger"],
    ["Período inicial", periodStart],
    ["Corte de tempo", "24x7"],
    ["Período final", periodEnd],
    ["Duração analisada", duration(totalSeconds)],
    ["Hosts avaliados", String(result.host_count)],
    ["Itens calculados", String(result.calculated_count)],
  ], 42, 628)
  addPdfCard(summary, 42, 548, 118, "Disponibilidade média", availability, [0.03, 0.47, 0.34])
  addPdfCard(summary, 172, 548, 118, "Indisponibilidade", problem, [0.75, 0.07, 0.24])
  addPdfCard(summary, 302, 548, 118, "Incidentes", String(result.total_incident_count), [0.78, 0.39, 0.02])
  addPdfCard(summary, 432, 548, 118, "Maior indisponibilidade", duration(result.max_problem_seconds), [0.75, 0.07, 0.24])

  addText(summary, 42, 520, "Composição do período", 12, true)
  addPdfDonut(summary, 155, 410, 72, 36, availabilityValue)
  addRect(summary, 104, 316, 8, 8, true, [0.03, 0.47, 0.34])
  addText(summary, 118, 317, "Disponível", 7, true, [0.03, 0.47, 0.34])
  addRect(summary, 196, 316, 8, 8, true, [0.75, 0.07, 0.24])
  addText(summary, 210, 317, "Indisponível", 7, true, [0.75, 0.07, 0.24])
  addText(summary, 318, 455, "Tempo disponível", 8, true)
  addText(summary, 438, 455, duration(okSeconds), 8)
  addText(summary, 318, 430, "Tempo indisponível", 8, true)
  addText(summary, 438, 430, duration(problemSeconds), 8)
  addText(summary, 318, 405, "Menor disponibilidade", 8, true)
  addText(summary, 438, 405, worst, 8, false, [0.75, 0.07, 0.24])
  addText(summary, 318, 380, "Maior disponibilidade", 8, true)
  addText(summary, 438, 380, best, 8, false, [0.03, 0.47, 0.34])
  addPdfBar(summary, 318, 350, 220, 14, availabilityValue)

  addText(summary, 42, 245, "Itens com menor disponibilidade", 12, true)
  addText(summary, 42, 229, stablePeriod ? "Todos os itens do recorte estão em 100% de disponibilidade." : "Itens com menor disponibilidade no recorte analisado.", 8, false, [0.33, 0.39, 0.47])
  addPdfWorstTable(summary, worstRows, 42, 208)
  addPdfFooter(summary, generatedAt, 1, totalPages)
  pages.push(summary.join("\n"))

  detailChunks.forEach((chunk, index) => {
    const page: string[] = []
    addPdfHeader(page, "Detalhamento por Host", "Resultados individuais", generatedAt)
    addPdfCard(page, 42, 710, 118, "Host", hostBase, [0.09, 0.13, 0.20])
    addPdfCard(page, 172, 710, 118, "Linhas", String(detailRows.length), [0.09, 0.13, 0.20])
    addPdfCard(page, 302, 710, 118, "Disponibilidade geral", availability, [0.03, 0.47, 0.34])
    addPdfCard(page, 432, 710, 118, "Incidentes", String(result.total_incident_count), [0.03, 0.47, 0.34])
    addText(page, 42, 665, `Total de linhas: ${detailRows.length}`, 10, true)
    addText(page, 155, 665, `Base: ${hostBase} - manutenção não considerada`, 8, false, [0.33, 0.39, 0.47])
    addPdfDetailTable(page, chunk, 42, 646)
    addPdfFooter(page, generatedAt, index + 2, totalPages)
    pages.push(page.join("\n"))
  })

  return encodePdf(pages)
}

function buildAvailabilityPdf(result: GroupTriggerAvailability) {
  const availability = percent(result.average_availability_percent)
  const availabilityValue = result.average_availability_percent ?? 0
  const problemValue = Math.max(0, 100 - availabilityValue)
  const problem = percent(result.average_availability_percent === null ? null : problemValue)
  const worst = percent(result.worst_availability_percent)
  const best = percent(result.best_availability_percent)
  const periodStart = result.results[0]?.period_start ? clock(result.results[0].period_start) : "-"
  const periodEnd = result.results[0]?.period_end ? clock(result.results[0].period_end) : "-"
  const generatedAt = new Date().toLocaleString("pt-BR")
  const totalSeconds = result.results.reduce((sum, row) => sum + row.total_seconds, 0)
  const problemSeconds = result.results.reduce((sum, row) => sum + row.problem_seconds, 0)
  const okSeconds = Math.max(0, totalSeconds - problemSeconds)
  const rows = result.results.map((row) => [
    row.hosts[0]?.name ?? row.hosts[0]?.host ?? "não identificado",
    row.trigger_name,
    percent(row.problem_percent),
    percent(row.availability_percent),
    duration(row.problem_seconds),
    String(row.incident_count),
  ])

  const firstPage = 18
  const otherPages = 30
  const chunks: string[][][] = [rows.slice(0, firstPage)]
  for (let index = firstPage; index < rows.length; index += otherPages) {
    chunks.push(rows.slice(index, index + otherPages))
  }

  const pages = chunks.map((chunk, pageIndex) => {
    const commands: string[] = []
    addText(commands, 42, 805, "Visão Detalhada", 18, true)
    addRect(commands, 42, 720, 512, 62, false, [0.48, 0.53, 0.58])
    addText(commands, 54, 762, "Consulta", 8, true)
    addText(commands, 120, 762, "Relatório de disponibilidade", 8)
    addText(commands, 320, 762, "Corte de tempo", 8, true)
    addText(commands, 410, 762, "24x7", 8)
    addText(commands, 54, 746, "Itens", 8, true)
    addText(commands, 120, 746, `${result.calculated_count} item(ns) calculado(s)`, 8)
    addText(commands, 320, 746, "Duração", 8, true)
    addText(commands, 410, 746, duration(totalSeconds), 8)
    addText(commands, 54, 730, "Hosts", 8, true)
    addText(commands, 120, 730, `${result.host_count} host(s)`, 8)
    addText(commands, 320, 730, "Período", 8, true)
    addText(commands, 410, 730, `${periodStart} a ${periodEnd}`, 8)

    if (pageIndex === 0) {
      addPie(commands, 170, 610, 82, availabilityValue)
      addRect(commands, 92, 500, 8, 8, true, [0.44, 0.63, 0.44])
      addText(commands, 106, 502, "OK", 8, true)
      addRect(commands, 150, 500, 8, 8, true, [0.79, 0.30, 0.33])
      addText(commands, 164, 502, "CRÍTICO", 8, true)

      addMetric(commands, 330, 665, "Disponibilidade média", availability, [0.03, 0.47, 0.34])
      addMetric(commands, 330, 625, "Indisponibilidade média", problem, [0.75, 0.07, 0.24])
      addMetric(commands, 330, 585, "Hosts / itens", `${result.host_count} / ${result.calculated_count}`)
      addMetric(commands, 330, 545, "Incidentes", String(result.total_incident_count))
      addMetric(commands, 442, 585, "Menor disponibilidade", worst, [0.75, 0.07, 0.24])
      addMetric(commands, 442, 545, "Maior disponibilidade", best, [0.03, 0.47, 0.34])

      addText(commands, 42, 470, "Resumo por estado", 12, true)
      addStateRow(commands, 42, 448, "OK", duration(okSeconds), availability, [0.40, 0.77, 0.40])
      addStateRow(commands, 42, 428, "CRÍTICO", duration(problemSeconds), problem, [0.94, 0.27, 0.27])
      addText(commands, 42, 395, "Detalhamento por host", 12, true)
      addTable(commands, chunk, 42, 374)
    } else {
      addText(commands, 42, 690, "Detalhamento por host", 12, true)
      addTable(commands, chunk, 42, 669)
    }

    addLine(commands, 42, 28, 554, 28, [0.07, 0.09, 0.14])
    addText(commands, 42, 16, `Gerado em ${generatedAt} - Manutenção não considerada. Página ${pageIndex + 1}/${chunks.length}`, 8)
    return commands.join("\n")
  })

  return encodePdf(pages)
}

// Monta o arquivo PDF final (cabecalho + objetos + xref) a partir dos
// comandos de desenho gerados pelas funcoes addX() abaixo. As funcoes
// addText/addRect/addLine/addPie/etc. sao primitivas de baixo nivel do
// gerador de PDF manual; os nomes ja sao autoexplicativos.
function encodePdf(pageContents: string[]) {
  const objects: string[] = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "",
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
  ]
  const pageIds: number[] = []
  for (const content of pageContents) {
    const pageId = objects.length + 1
    const contentId = objects.length + 2
    pageIds.push(pageId)
    objects.push(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents ${contentId} 0 R >>`)
    objects.push(`<< /Length ${content.length} >>\nstream\n${content}\nendstream`)
  }
  objects[1] = `<< /Type /Pages /Kids [${pageIds.map((id) => `${id} 0 R`).join(" ")}] /Count ${pageIds.length} >>`

  let pdf = "%PDF-1.4\n"
  const offsets = [0]
  objects.forEach((object, index) => {
    offsets.push(pdf.length)
    pdf += `${index + 1} 0 obj\n${object}\nendobj\n`
  })
  const xrefOffset = pdf.length
  pdf += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`
  for (let index = 1; index <= objects.length; index += 1) {
    pdf += `${String(offsets[index]).padStart(10, "0")} 00000 n \n`
  }
  pdf += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xrefOffset}\n%%EOF`
  return new TextEncoder().encode(pdf)
}

function addText(commands: string[], x: number, y: number, text: string, size = 9, bold = false, color: [number, number, number] = [0.09, 0.13, 0.20]) {
  commands.push(`${color.join(" ")} rg`)
  commands.push(`BT /${bold ? "F2" : "F1"} ${size} Tf ${x} ${y} Td ${pdfText(text)} Tj ET`)
}

function addRect(commands: string[], x: number, y: number, width: number, height: number, fill: boolean, color: [number, number, number]) {
  commands.push(`${color.join(" ")} ${fill ? "rg" : "RG"}`)
  commands.push(`${x} ${y} ${width} ${height} re ${fill ? "f" : "S"}`)
}

function addLine(commands: string[], x1: number, y1: number, x2: number, y2: number, color: [number, number, number]) {
  commands.push(`${color.join(" ")} RG`)
  commands.push(`${x1} ${y1} m ${x2} ${y2} l S`)
}

function addPdfHeader(commands: string[], title: string, subtitle: string, generatedAt: string) {
  addRect(commands, 0, 792, 595, 50, true, [0.05, 0.12, 0.20])
  addText(commands, 42, 814, title, 17, true, [1, 1, 1])
  addText(commands, 42, 800, subtitle, 8, false, [0.82, 0.88, 0.95])
  addText(commands, 430, 800, `Gerado em ${generatedAt}`, 7, false, [0.82, 0.88, 0.95])
}

function addResultBanner(commands: string[], title: string, message: string, badge: string, badgeColor: [number, number, number], x: number, y: number) {
  addRect(commands, x, y, 512, 52, true, [0.95, 0.99, 0.98])
  addRect(commands, x, y, 512, 52, false, [0.73, 0.88, 0.84])
  addText(commands, x + 16, y + 31, title, 11, true, [0.05, 0.12, 0.20])
  addText(commands, x + 16, y + 16, truncate(message, 82), 8, false, [0.33, 0.39, 0.47])
  addRoundPill(commands, x + 394, y + 18, 92, 18, badgeColor)
  addText(commands, x + 408, y + 24, badge, 6, true, [1, 1, 1])
}

function addRoundPill(commands: string[], x: number, y: number, width: number, height: number, color: [number, number, number]) {
  addRect(commands, x, y, width, height, true, color)
}

function addPdfInfo(commands: string[], items: Array<[string, string]>, x: number, y: number) {
  addRect(commands, x, y, 512, 94, true, [0.97, 0.98, 0.99])
  addRect(commands, x, y, 512, 94, false, [0.77, 0.81, 0.86])
  items.forEach(([label, value], index) => {
    const col = index % 2
    const row = Math.floor(index / 2)
    const itemX = x + 14 + col * 260
    const itemY = y + 72 - row * 20
    addText(commands, itemX, itemY, label, 7, true, [0.33, 0.39, 0.47])
    addText(commands, itemX + 92, itemY, truncate(value, 34), 8)
  })
}

function addPdfCard(commands: string[], x: number, y: number, width: number, label: string, value: string, color: [number, number, number]) {
  addRect(commands, x, y, width, 44, true, [0.98, 0.99, 1])
  addRect(commands, x, y, width, 44, false, [0.82, 0.86, 0.91])
  addRect(commands, x, y, 4, 44, true, color)
  addText(commands, x + 12, y + 27, label, 7, false, [0.39, 0.45, 0.55])
  const valueSize = value.length > 18 ? 10 : 13
  const maxChars = Math.max(8, Math.floor((width - 20) / (valueSize * 0.54)))
  addText(commands, x + 12, y + 10, truncate(value, maxChars), valueSize, true, color)
}

function addPdfBar(commands: string[], x: number, y: number, width: number, height: number, availability: number) {
  const okWidth = Math.max(0, Math.min(width, (width * availability) / 100))
  addRect(commands, x, y, width, height, true, [0.90, 0.92, 0.95])
  addRect(commands, x, y, okWidth, height, true, [0.10, 0.72, 0.49])
  if (okWidth < width) addRect(commands, x + okWidth, y, width - okWidth, height, true, [0.88, 0.16, 0.30])
  addRect(commands, x, y, width, height, false, [0.70, 0.75, 0.82])
}

function addPdfDonut(commands: string[], cx: number, cy: number, radius: number, innerRadius: number, availability: number) {
  addCirclePolygon(commands, cx, cy, radius, [0.10, 0.72, 0.49])
  if (availability < 100) {
    const start = -90 + (Math.max(0, availability) / 100) * 360
    addSector(commands, cx, cy, radius, start, 270, [0.88, 0.16, 0.30])
  }
  addCirclePolygon(commands, cx, cy, innerRadius, [1, 1, 1])
  const value = `${availability.toFixed(2)}%`
  const valueSize = value.length > 7 ? 11 : 12
  addText(commands, cx - approximateTextWidth(value, valueSize) / 2, cy + 2, value, valueSize, true, [0.03, 0.47, 0.34])
  addText(commands, cx - 16, cy - 11, "disp.", 6, false, [0.39, 0.45, 0.55])
}

function addPdfWorstTable(commands: string[], rows: string[][], x: number, y: number) {
  const widths = [112, 218, 82, 82]
  const headers = ["Host", "Item monitorado", "Disponibilidade", "Indisponibilidade"]
  addPdfGenericTable(commands, rows, headers, widths, x, y, 20, 8, ["default", "default", "availability", "problem"])
}

function addPdfDetailTable(commands: string[], rows: string[][], x: number, y: number) {
  const widths = [112, 174, 58, 58, 86, 44]
  const headers = ["Host", "Item monitorado", "Indisp.", "Disp.", "Tempo indisponível", "Incidentes"]
  addPdfGenericTable(commands, rows, headers, widths, x, y, 18, 7, ["default", "default", "problem", "availability", "default", "default"])
}

function addPdfGenericTable(commands: string[], rows: string[][], headers: string[], widths: number[], x: number, y: number, rowHeight: number, fontSize: number, columnTypes: Array<"default" | "problem" | "availability">) {
  const totalWidth = widths.reduce((sum, width) => sum + width, 0)
  let currentX = x
  addRect(commands, x, y, totalWidth, rowHeight, true, [0.88, 0.92, 0.96])
  headers.forEach((header, index) => {
    addText(commands, currentX + 4, y + 7, header, fontSize, true, [0.19, 0.25, 0.34])
    currentX += widths[index]
  })
  rows.forEach((row, rowIndex) => {
    const rowY = y - rowHeight * (rowIndex + 1)
    if (rowIndex % 2 === 0) addRect(commands, x, rowY, totalWidth, rowHeight, true, [0.98, 0.99, 1])
    addLine(commands, x, rowY, x + totalWidth, rowY, [0.88, 0.90, 0.93])
    currentX = x
    row.forEach((cell, index) => {
      const maxLength = index === 0 ? Math.max(10, Math.floor(widths[index] / 5.2)) : index === 1 ? Math.max(12, Math.floor(widths[index] / 4.4)) : Math.max(6, Math.floor(widths[index] / 4.8))
      const text = truncate(cell, maxLength)
      const type = columnTypes[index] ?? "default"
      const color: [number, number, number] = type === "problem" ? [0.75, 0.07, 0.24] : type === "availability" ? [0.03, 0.47, 0.34] : [0.09, 0.13, 0.20]
      addText(commands, currentX + 4, rowY + 6, text, fontSize, type !== "default", color)
      currentX += widths[index]
    })
  })
}

function addPdfFooter(commands: string[], generatedAt: string, page: number, totalPages: number) {
  addLine(commands, 42, 28, 554, 28, [0.07, 0.09, 0.14])
  addText(commands, 42, 16, `Gerado em ${generatedAt} - Manutenção não considerada`, 8, false, [0.39, 0.45, 0.55])
  addText(commands, 515, 16, `${page}/${totalPages}`, 8, true, [0.39, 0.45, 0.55])
}

function addMetric(commands: string[], x: number, y: number, label: string, value: string, color: [number, number, number] = [0.09, 0.13, 0.20]) {
  addRect(commands, x, y, 100, 30, false, [0.80, 0.84, 0.88])
  addText(commands, x + 6, y + 19, label, 6, false, [0.39, 0.45, 0.55])
  addText(commands, x + 6, y + 6, value, 11, true, color)
}

function addStateRow(commands: string[], x: number, y: number, state: string, time: string, value: string, color: [number, number, number]) {
  addRect(commands, x, y, 92, 18, true, color)
  addRect(commands, x, y, 360, 18, false, [0.58, 0.64, 0.72])
  addText(commands, x + 5, y + 5, state, 8, true, state === "CRÍTICO" ? [1, 1, 1] : [0.04, 0.17, 0.07])
  addText(commands, x + 110, y + 5, time, 8)
  addText(commands, x + 250, y + 5, value, 8)
}

function addTable(commands: string[], rows: string[][], x: number, y: number) {
  const widths = [82, 178, 62, 62, 86, 52]
  const headers = ["Host", "Item monitorado", "Indisp.", "Disp.", "Tempo indisponível", "Incidentes"]
  let currentX = x
  addRect(commands, x, y, widths.reduce((sum, width) => sum + width, 0), 18, true, [0.89, 0.92, 0.95])
  headers.forEach((header, index) => {
    addText(commands, currentX + 4, y + 6, header, 7, true, [0.20, 0.25, 0.33])
    currentX += widths[index]
  })
  let rowY = y - 18
  rows.forEach((row) => {
    currentX = x
    addLine(commands, x, rowY, x + widths.reduce((sum, width) => sum + width, 0), rowY, [0.88, 0.90, 0.93])
    row.forEach((cell, index) => {
      const text = index === 1 ? truncate(cell, 48) : truncate(cell, 22)
      const color: [number, number, number] = index === 2 ? [0.75, 0.07, 0.24] : index === 3 ? [0.03, 0.47, 0.34] : [0.09, 0.13, 0.20]
      addText(commands, currentX + 4, rowY + 6, text, 7, index === 2 || index === 3, color)
      currentX += widths[index]
    })
    rowY -= 18
  })
}

function addPie(commands: string[], cx: number, cy: number, radius: number, availability: number) {
  addCirclePolygon(commands, cx, cy, radius, [0.44, 0.63, 0.44])
  if (availability < 100) {
    const start = -90 + (Math.max(0, availability) / 100) * 360
    addSector(commands, cx, cy, radius, start, 270, [0.79, 0.30, 0.33])
  }
}

function addCirclePolygon(commands: string[], cx: number, cy: number, radius: number, color: [number, number, number]) {
  const points = Array.from({ length: 50 }, (_, index) => {
    const angle = (index / 50) * Math.PI * 2
    return [cx + Math.cos(angle) * radius, cy + Math.sin(angle) * radius]
  })
  addPolygon(commands, points, color)
}

function addSector(commands: string[], cx: number, cy: number, radius: number, startDegrees: number, endDegrees: number, color: [number, number, number]) {
  const points: number[][] = [[cx, cy]]
  const steps = 24
  for (let index = 0; index <= steps; index += 1) {
    const angle = (startDegrees + ((endDegrees - startDegrees) * index) / steps) * Math.PI / 180
    points.push([cx + Math.cos(angle) * radius, cy + Math.sin(angle) * radius])
  }
  addPolygon(commands, points, color)
}

function addPolygon(commands: string[], points: number[][], color: [number, number, number]) {
  const [first, ...rest] = points
  commands.push(`${color.join(" ")} rg`)
  commands.push(`${first[0].toFixed(2)} ${first[1].toFixed(2)} m ${rest.map(([x, y]) => `${x.toFixed(2)} ${y.toFixed(2)} l`).join(" ")} h f`)
}

function pdfText(value: string) {
  const hex = Array.from(value).map((char) => {
    const code = char.charCodeAt(0)
    return (code <= 255 ? code : 63).toString(16).padStart(2, "0")
  }).join("")
  return `<${hex}>`
}

function approximateTextWidth(value: string, fontSize: number) {
  return value.length * fontSize * 0.52
}

function truncate(value: string, maxLength: number) {
  return value.length > maxLength ? `${value.slice(0, maxLength - 3)}...` : value
}

function uniqueText(values: string[]) {
  const uniqueValues = Array.from(new Set(values.filter(Boolean)))
  if (uniqueValues.length === 0) return "não identificado"
  if (uniqueValues.length === 1) return uniqueValues[0]
  return "Múltiplos hosts"
}

function csvCell(value: string) {
  const normalized = value.replace(/\r?\n/g, " ").trim()
  if (/[;"\r\n]/.test(normalized)) {
    return `"${normalized.replace(/"/g, '""')}"`
  }
  return normalized
}

function csvPercent(value: number | null) {
  return value === null ? "" : value.toFixed(4).replace(".", ",")
}

// Formata datas/horas vindas da API (sempre ISO 8601 "AAAA-MM-DDTHH:mm:ss",
// já no fuso horário escolhido no filtro -- ver PeriodInputs) para o padrão
// brasileiro "DD/MM/AAAA HH:mm:ss". Feito com regex em vez de `new Date()`
// de proposito: `new Date("...sem timezone...")` é interpretado pelo
// navegador no SEU fuso local, o que reinterpretaria a hora errado se o
// navegador de quem está vendo o relatório estiver em outro fuso horário.
function clock(value: string) {
  const match = value.match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})/)
  if (!match) return value.replace("T", " ").slice(0, 19)
  const [, year, month, day, hours, minutes, seconds] = match
  return `${day}/${month}/${year} ${hours}:${minutes}:${seconds}`
}

function initialStateLabel(value: string) {
  if (value === "ASSUMED_OK") return "Disponivel"
  if (value === "PROBLEM") return "Indisponivel"
  if (value === "UNKNOWN") return "Nao identificado"
  return "Disponivel"
}

// Linha de detalhe de um unico resultado (host/trigger) na tabela.
function AvailabilityResultRow({ row }: { row: AvailabilityResult }) {
  const [open, setOpen] = useState(false)
  const [timeline, setTimeline] = useState<Timeline | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")

  async function toggleAudit() {
    const nextOpen = !open
    setOpen(nextOpen)
    if (!nextOpen || timeline || loading) return
    setLoading(true)
    setError("")
    try {
      setTimeline(await getTimeline({
        triggerid: row.triggerid,
        period_start: row.period_start,
        period_end: row.period_end,
        timezone: row.timezone,
      }))
    } catch (caught) {
      setError(errorText(caught))
    } finally {
      setLoading(false)
    }
  }

  return (
    <>
      <tr className="border-b border-slate-100 dark:border-slate-800">
        <td className="py-3 pr-3 font-medium text-slate-950 dark:text-white">{row.hosts[0]?.name ?? "nao identificado"}</td>
        <td className="max-w-[440px] py-3 pr-3 text-slate-700 dark:text-slate-200">{row.trigger_name}</td>
        <td className="py-3 pr-3 font-semibold tabular-nums text-rose-600 dark:text-rose-400">{percent(row.problem_percent)}</td>
        <td className="py-3 pr-3 font-semibold text-emerald-700 dark:text-emerald-400">{percent(row.availability_percent)}</td>
        <td className="py-3 pr-3 tabular-nums">{duration(row.problem_seconds)}</td>
        <td className="py-3 pr-3 tabular-nums">{row.incident_count}</td>
        <td className="py-3 text-center">
          <button
            aria-expanded={open}
            aria-label={`${open ? "Fechar" : "Abrir"} auditoria de ${row.trigger_name}`}
            className="mx-auto grid h-8 w-8 place-items-center rounded-md border border-slate-300 text-sm text-slate-600 transition hover:border-emerald-500 hover:text-emerald-600 dark:border-[#39414d] dark:text-slate-300 dark:hover:border-emerald-400 dark:hover:text-emerald-400"
            onClick={() => void toggleAudit()}
            type="button"
          >
            {open ? "^" : "v"}
          </button>
        </td>
      </tr>
      {open && (
        <tr className="border-b border-slate-100 bg-slate-50 dark:border-slate-800 dark:bg-[#08090d]">
          <td className="px-4 py-4" colSpan={7}>
            <div className="border-l-2 border-emerald-500 pl-4 text-xs">
              {loading && <span>Carregando auditoria...</span>}
              {error && <span className="text-rose-600">{error}</span>}
              {timeline && (
                <div className="grid gap-2">
                  <span>ID interno: {row.triggerid}</span>
                  <span>Historico anterior: {timeline.audit.previous_event_found ? "Encontrado" : "Nao encontrado"}</span>
                  <span>Eventos no periodo: {timeline.audit.events_in_window_count}</span>
                  <span>Estado inicial: {initialStateLabel(timeline.result.initial_state)}</span>
                  <span>Manutencao: {timeline.audit.maintenance_considered ? "Considerada" : "Nao considerada"}</span>
                  <TimelineTable intervals={timeline.intervals} compact />
                </div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

// Tabela com a linha do tempo detalhada (intervalos OK/PROBLEM) de uma
// trigger especifica.
function TimelineTable({ intervals, compact = false }: { intervals: Timeline["intervals"]; compact?: boolean }) {
  return (
    <div className="mt-2 overflow-auto">
      <p className="mb-2 text-[11px] font-semibold uppercase text-slate-500 dark:text-slate-400">Linha do tempo utilizada</p>
      <table className={`w-full border-collapse text-left ${compact ? "text-xs" : "text-sm"}`}>
        <thead className="text-[11px] uppercase text-slate-500 dark:text-slate-400">
          <tr className="border-b border-slate-200 dark:border-slate-700">
            <th className="py-2 pr-3">Inicio</th>
            <th className="py-2 pr-3">Fim</th>
            <th className="py-2 pr-3">Estado</th>
            <th className="py-2 pr-3">Duracao</th>
          </tr>
        </thead>
        <tbody>
          {intervals.map((interval, index) => (
            <tr key={`${interval.source_eventid}-${index}`} className="border-b border-slate-100 dark:border-slate-800">
              <td className="py-2 pr-3">{clock(interval.interval_start)}</td>
              <td className="py-2 pr-3">{clock(interval.interval_end)}</td>
              <td className="py-2 pr-3">{timelineStateLabel(interval.state)}</td>
              <td className="py-2 pr-3">{duration(interval.duration_seconds)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function timelineStateLabel(value: string) {
  if (value === "PROBLEM") return "Indisponivel"
  if (value === "UNKNOWN") return "Nao identificado"
  return "Disponivel"
}

// Le o tema salvo (ou o preferido pelo SO, na primeira visita).
function readTheme(): Theme {
  try {
    return localStorage.getItem(themeStorageKey) === "dark" ? "dark" : "light"
  } catch {
    return "light"
  }
}

// Traduz um erro (ApiRequestError ou generico) na mensagem exibida ao
// usuario, incluindo o request id para correlacionar com os logs do
// backend quando fizer sentido (ver logger.ts / api.ts).
function errorText(error: unknown) {
  if (error instanceof ApiRequestError) {
    if (error.status === 0) return withRequestId(error.message, error.requestId)
    if (error.status === 401 && error.message.includes("Sessao")) return "Sessao expirada. Entre novamente."
    if (error.status === 401) return "Usuario ou senha invalidos."
    if (error.status === 400) return error.message
    if (error.status === 422) return "Preencha os campos obrigatorios antes de continuar."
    if (error.status === 502) {
      const detail = typeof error.details === "string" && error.details.trim() ? ` Detalhe: ${error.details.trim()}` : ""
      return withRequestId(`Nao foi possivel acessar o Zabbix.${detail} Verifique a conexao e tente novamente.`, error.requestId)
    }
    if (error.message.includes("triggers nao foram encontradas")) return "Nenhuma trigger encontrada para os filtros selecionados."
    return withRequestId(error.message, error.requestId)
  }
  return error instanceof Error ? error.message : "Falha inesperada."
}

// Anexa "(ID: ...)" a mensagem de erro, quando houver um request id.
function withRequestId(message: string, requestId?: string) {
  return requestId ? `${message} (ID: ${requestId})` : message
}
