import React, { useEffect, useState } from 'react'
import {
  getAiConfig,
  updateAiConfig,
  testAiConnection,
  getFeedbackStatus,
  clearFeedback,
  type AiConfig,
  type AiStatus,
  type AiTestResponse,
  type FeedbackStats,
} from '../api/client'

/** Mensaje legible de un error axios (detail del backend si existe). */
const detailOf = (err: unknown): string => {
  const d = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (typeof d === 'string' && d) return d
  return err instanceof Error ? err.message : 'Error desconocido'
}

type SaveState = 'idle' | 'saving' | 'saved'

/**
 * Panel «Opciones IA»: conexión con el proveedor cloud (endpoint compatible
 * con OpenAI: Groq, etc.), modelo y parámetros de generación.
 *
 * Todo se aplica SIN reiniciar: el backend relee el JSON de configuración en
 * la próxima llamada. Si el proveedor está caído, el asistente y el informe
 * siguen funcionando con el motor determinista (degradación elegente).
 */
const AiOptions: React.FC = () => {
  const [cfg, setCfg] = useState<AiConfig | null>(null)
  const [saved, setSaved] = useState<AiConfig | null>(null)
  const [status, setStatus] = useState<AiStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [save, setSave] = useState<SaveState>('idle')
  const [error, setError] = useState<string | null>(null)
  const [testing, setTesting] = useState(false)
  const [test, setTest] = useState<AiTestResponse | null>(null)
  /** Memoria de criterio (few-shot): ejemplos aprendidos del analista. */
  const [fb, setFb] = useState<FeedbackStats | null>(null)
  const [fbBusy, setFbBusy] = useState(false)

  const refreshFeedback = async () => {
    try {
      setFb(await getFeedbackStatus())
    } catch {
      setFb(null)   // sin memoria: el panel la oculta, nada más
    }
  }

  const onClearFeedback = async () => {
    setFbBusy(true)
    try {
      const res = await clearFeedback()
      setFb(res.stats)
    } catch {
      /* no se pudo vaciar: se reintenta con el próximo montaje */
    } finally {
      setFbBusy(false)
    }
  }

  const refresh = async () => {
    const data = await getAiConfig()
    setCfg(data.config)
    setSaved(data.config)
    setStatus(data.status)
    return data
  }

  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        await refresh()
      } catch (err) {
        if (alive) setLoadError(detailOf(err))
      } finally {
        if (alive) setLoading(false)
      }
      // Paralelo e independiente: si falla, el panel simplemente no la muestra.
      if (alive) void refreshFeedback()
    })()
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const dirty = !!cfg && !!saved && JSON.stringify(cfg) !== JSON.stringify(saved)

  const patch = (p: Partial<AiConfig>) => {
    setCfg((c) => (c ? { ...c, ...p } : c))
    setSave('idle')
    setError(null)
  }

  const numberChange =
    (key: keyof AiConfig) => (e: React.ChangeEvent<HTMLInputElement>) => {
      const raw = e.target.value
      if (raw === '') return // no vaciar el campo a mitad de tipeo
      const value = Number(raw)
      if (!Number.isNaN(value)) patch({ [key]: value } as Partial<AiConfig>)
    }

  const onSave = async () => {
    if (!cfg) return
    setSave('saving')
    setError(null)
    try {
      const data = await updateAiConfig(cfg)
      setCfg(data.config)
      setSaved(data.config)
      setStatus(data.status)
      setSave('saved')
      window.setTimeout(() => setSave((s) => (s === 'saved' ? 'idle' : s)), 4000)
    } catch (err) {
      setSave('idle')
      setError(detailOf(err))
    }
  }

  const onTest = async () => {
    if (!cfg) return
    setTesting(true)
    setTest(null)
    setError(null)
    try {
      const data = await testAiConnection(cfg) // prueba los valores del panel sin guardar
      setTest(data)
      if (data.status) setStatus(data.status)
    } catch (err) {
      setError(detailOf(err))
    } finally {
      setTesting(false)
    }
  }

  if (loading) {
    return <p className="hud-label">Cargando configuración de la IA…</p>
  }
  if (loadError || !cfg) {
    return (
      <div className="panel">
        <div className="panel-body">
          <p className="hud-data text-rose-400">
            No se pudo leer la configuración: {loadError ?? 'respuesta vacía del backend'}
          </p>
        </div>
      </div>
    )
  }

  const remoteUp = status?.kind === 'remote' && status.reachable === true
  const selectedModel = status?.models.find((m) => m.name === cfg.model)

  const chip = !cfg.enabled
    ? { color: '#7280a3', label: 'LLM apagado · motor determinista' }
    : status === null
      ? { color: '#7280a3', label: 'Sondeando el proveedor…' }
      : status.reachable === true
        ? { color: '#10b981', label: `Proveedor conectado · ${status.models.length} modelos` }
        : status.reachable === false
          ? { color: '#f87171', label: 'Proveedor no responde' }
          : { color: '#38bdf8', label: 'Proveedor cloud (sin inspección local)' }

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      {/* ------------------------------------------------------------- */}
      {/* Motor de IA: estado, encendido, modelo y URL                  */}
      {/* ------------------------------------------------------------- */}
      <div className="panel">
        <div className="panel-head">
          <h2 className="panel-title">Motor de IA</h2>
          <span
            className="chip"
            style={{ color: chip.color, background: `${chip.color}14`, boxShadow: `0 0 10px ${chip.color}33` }}
            aria-live="polite"
          >
            <span className="h-2 w-2 rounded-full" style={{ background: chip.color }} />
            {chip.label}
          </span>
        </div>
        <div className="panel-body space-y-4">
          {/* Interruptor LLM on/off */}
          <div className="flex flex-wrap items-center justify-between gap-3 border border-hud-soft bg-inset/60 px-4 py-3 [clip-path:var(--chamfer-sm)]">
            <div className="min-w-0">
              <p className="hud-data text-sm">Usar modelo de lenguaje (LLM)</p>
              <p className="mt-1 text-xs text-[var(--text-dim)]">
                Encendido: el modelo redacta respuestas e informes con las evidencias recuperadas.
                Apagado: todo lo resuelve el motor determinista local.
              </p>
            </div>
            <button
              role="switch"
              aria-checked={cfg.enabled}
              aria-label="Activar o desactivar el LLM"
              onClick={() => patch({ enabled: !cfg.enabled })}
              className={`relative h-7 w-14 flex-shrink-0 border transition-colors [clip-path:var(--chamfer-sm)] ${
                cfg.enabled
                  ? 'border-emerald-400/70 bg-emerald-500/25 shadow-[0_0_10px_rgba(16,185,129,0.35)]'
                  : 'border-hud-soft bg-inset'
              }`}
            >
              <span
                className={`absolute top-1/2 h-5 w-5 -translate-y-1/2 rounded-sm transition-all ${
                  cfg.enabled ? 'left-8 bg-emerald-400' : 'left-1 bg-hud-muted'
                }`}
              />
            </button>
          </div>

          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            {/* Modelo */}
            <div>
              <label className="hud-label" htmlFor="ai-model">
                Modelo
              </label>
              <input
                id="ai-model"
                list="ai-models"
                className="hud-input mt-2 w-full"
                value={cfg.model}
                onChange={(e) => patch({ model: e.target.value })}
                placeholder="openai/gpt-oss-120b"
                spellCheck={false}
              />
              <datalist id="ai-models">
                {(status?.models ?? []).map((m) => (
                  <option key={m.name} value={m.name} />
                ))}
              </datalist>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                {remoteUp && selectedModel && (
                  <span className="chip text-emerald-400" style={{ background: '#10b98114' }}>
                    Disponible en el proveedor
                  </span>
                )}
                {cfg.enabled && status?.reachable === true && !selectedModel && (
                  <span className="chip text-amber-400" style={{ background: '#ffb02014' }}>
                    No figura en la lista del proveedor
                  </span>
                )}
              </div>
            </div>

            {/* URL */}
            <div>
              <label className="hud-label" htmlFor="ai-url">
                Endpoint del LLM
              </label>
              <input
                id="ai-url"
                className="hud-input mt-2 w-full"
                value={cfg.api_url}
                onChange={(e) => patch({ api_url: e.target.value })}
                spellCheck={false}
              />
              <p className="mt-2 text-xs text-[var(--text-dim)]">
                Endpoint compatible con OpenAI (Groq, OpenRouter, Together…). Ej.:{' '}
                <code>https://api.groq.com/openai/v1/chat/completions</code>
              </p>
            </div>
          </div>

          {/* API key (proveedor cloud) */}
          <div>
            <label className="hud-label" htmlFor="ai-apikey">
              API key del proveedor
            </label>
            <input
              id="ai-apikey"
              type="password"
              className="hud-input mt-2 w-full md:w-96"
              value={cfg.api_key}
              onChange={(e) => patch({ api_key: e.target.value })}
              placeholder="gsk_… (obligatoria para probar y usar el proveedor)"
              autoComplete="off"
              spellCheck={false}
            />
            <p className="mt-2 text-xs text-[var(--text-dim)]">
              Viaja como <code>Authorization: Bearer</code> en cada llamada. Se guarda en el JSON
              de configuración del backend, en claro: usala solo en tu máquina.
            </p>
          </div>

          {/* Avisos degradación / caída */}
          <div className="border border-hud-soft bg-inset/60 px-4 py-3 text-xs leading-relaxed text-[var(--text-dim)] [clip-path:var(--chamfer-sm)]">
            {status?.reachable === false ? (
              <span className="text-amber-400">
                ⚠ El proveedor no responde en {status.base ?? cfg.api_url} ({status.reason}). El
                asistente y el informe siguen funcionando con el motor determinista local; nada se
                pierde. Revisá la URL, la API key y el modelo.
              </span>
            ) : (
              <>
                Los cambios se aplican al instante, sin reiniciar nada: el motor recibe las opciones
                en cada petición y el backend relee la configuración guardada en la próxima
                consulta.
              </>
            )}
          </div>
        </div>
      </div>

      {/* ------------------------------------------------------------- */}
      {/* Parámetros de generación                                      */}
      {/* ------------------------------------------------------------- */}
      <div className="panel">
        <div className="panel-head">
          <h2 className="panel-title">Parámetros de generación</h2>
        </div>
        <div className="panel-body grid grid-cols-1 gap-x-6 gap-y-5 md:grid-cols-2">
          {/* Temperature */}
          <div>
            <div className="flex items-baseline justify-between">
              <label className="hud-label" htmlFor="ai-temp">
                Temperature
              </label>
              <span className="hud-data text-xs">{cfg.temperature.toFixed(2)}</span>
            </div>
            <input
              id="ai-temp"
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={cfg.temperature}
              onChange={(e) => patch({ temperature: Number(e.target.value) })}
              className="mt-3 w-full accent-[var(--hud-line)]"
            />
            <p className="mt-1 text-xs text-[var(--text-dim)]">
              SOC = evidencia, no ficción: 0.10–0.20 lo recomendado.
            </p>
          </div>

          {/* Timeout */}
          <div className="md:col-span-2">
            <label className="hud-label" htmlFor="ai-timeout">
              Espera máxima por llamada (timeout, segundos)
            </label>
            <input
              id="ai-timeout"
              type="number"
              min={5}
              max={600}
              className="hud-input mt-2 w-full md:w-64"
              value={cfg.timeout}
              onChange={numberChange('timeout')}
            />
            <p className="mt-2 text-xs text-[var(--text-dim)]">
              El proveedor cloud responde en 2–6 s con gpt-oss-120b; 60 s deja margen holgado.
            </p>
          </div>
        </div>
      </div>

      {/* ------------------------------------------------------------- */}
      {/* Retención de análisis en disco (spec A)                       */}
      {/* ------------------------------------------------------------- */}
      <div className="panel">
        <div className="panel-head">
          <h2 className="panel-title">Retención de datos</h2>
        </div>
        <div className="panel-body">
          <label className="hud-label" htmlFor="ai-retention">
            Días antes de borrar un análisis del historial
          </label>
          <input
            id="ai-retention"
            type="number"
            min={0}
            max={3650}
            className="hud-input mt-2 w-full md:w-64"
            value={cfg.retention_days}
            onChange={numberChange('retention_days')}
          />
          <p className="mt-2 text-xs text-[var(--text-dim)]">
            Cada subida se guarda en disco (sobrevive a los reinicios del backend) y se
            borra a los N días sin usarse. <strong>0 = conservar para siempre</strong>.
            También puedes borrar análisis uno a uno desde «Historial de subidas».
          </p>
        </div>
      </div>

      {/* ------------------------------------------------------------- */}
      {/* Prueba de conexión                                            */}
      {/* ------------------------------------------------------------- */}
      <div className="panel">
        <div className="panel-head">
          <h2 className="panel-title">Prueba de conexión</h2>
          {test && (
            <span
              className="chip"
              style={{
                color: test.ok ? '#10b981' : '#f87171',
                background: `${test.ok ? '#10b981' : '#f87171'}14`,
              }}
            >
              {test.ok ? `Respuesta en ${test.latency_ms} ms` : 'Sin respuesta'}
            </span>
          )}
        </div>
        <div className="panel-body space-y-4">
          <div className="flex flex-wrap items-center gap-3">
            <button className="btn-hud" onClick={() => void onTest()} disabled={testing}>
              {testing ? 'Probando…' : 'Probar conexión'}
            </button>
            <span className="text-xs text-[var(--text-dim)]">
              Mide latencia real con los valores del panel (aún sin guardar) y confirma qué modelo
              respondió.
            </span>
          </div>

          {test && !test.ok && (
            <p className="hud-data text-sm text-rose-400">{test.error}</p>
          )}
          {test?.ok && (
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
              <div className="border border-hud-soft bg-inset/60 px-3 py-2 [clip-path:var(--chamfer-sm)]">
                <p className="hud-label">Latencia</p>
                <p className="hud-data mt-1 text-lg">{test.latency_ms} ms</p>
              </div>
              <div className="border border-hud-soft bg-inset/60 px-3 py-2 [clip-path:var(--chamfer-sm)]">
                <p className="hud-label">Modelo</p>
                <p className="hud-data mt-1 truncate text-sm" title={test.model}>
                  {test.model}
                </p>
              </div>
              <div className="border border-hud-soft bg-inset/60 px-3 py-2 [clip-path:var(--chamfer-sm)]">
                <p className="hud-label">Proveedor</p>
                <p className="hud-data mt-1 truncate text-sm" title={status?.base ?? cfg.api_url}>
                  {status?.base ?? cfg.api_url}
                </p>
              </div>
              <div className="col-span-full border border-emerald-400/30 bg-emerald-500/5 px-3 py-2 [clip-path:var(--chamfer-sm)]">
                <p className="hud-label text-emerald-400">Respuesta del modelo</p>
                <p className="hud-data mt-1 text-sm text-emerald-100/90">«{test.reply}»</p>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ------------------------------------------------------------- */}
      {/* Checklist del proveedor cloud                                 */}
      {/* ------------------------------------------------------------- */}
      <div className="panel">
        <div className="panel-head">
          <h2 className="panel-title">Checklist del proveedor cloud</h2>
        </div>
        <div className="panel-body">
          <ul className="grid grid-cols-1 gap-2 text-sm text-[var(--text-dim)] md:grid-cols-2">
            <li>
              <span
                className={
                  remoteUp ? 'text-emerald-400' : status?.reachable === false ? 'text-rose-400' : 'text-hud-muted'
                }
              >
                {remoteUp ? '✓' : status?.reachable === false ? '✗' : '…'}
              </span>{' '}
              Proveedor reachable ({status?.base ?? cfg.api_url})
            </li>
            <li>
              <span className={cfg.api_key ? 'text-emerald-400' : 'text-amber-400'}>
                {cfg.api_key ? '✓' : '!'}
              </span>{' '}
              API key configurada (viaja como <code>Authorization: Bearer</code>)
            </li>
            <li>
              <span className={selectedModel ? 'text-emerald-400' : 'text-hud-muted'}>
                {selectedModel ? '✓' : '…'}
              </span>{' '}
              Modelo en el catálogo del proveedor ({cfg.model || 'sin definir'})
            </li>
            <li>
              <span className="text-emerald-400">✓</span> Sin hardware local: GPU, RAM y VRAM no
              aplican — la inferencia corre en la nube
            </li>
          </ul>
          <p className="mt-4 text-xs text-[var(--text-dim)]">
            Sin proveedor, el proyecto funciona igual con el motor determinista: la IA es un plus,
            no un requisito.
          </p>
        </div>
      </div>

      {/* ------------------------------------------------------------- */}
      {/* Memoria de criterio (few-shot con feedback del analista)       */}
      {/* ------------------------------------------------------------- */}
      {fb && (
        <div className="panel">
          <div className="panel-head">
            <h2 className="panel-title">Memoria de criterio (RAG few-shot)</h2>
            <span
              className="chip"
              style={{
                color: fb.total > 0 ? '#1fc8ff' : 'var(--text-dim)',
                background: fb.total > 0 ? '#1fc8ff14' : 'transparent',
              }}
            >
              {fb.total} {fb.total === 1 ? 'ejemplo' : 'ejemplos'} aprendidos
            </span>
          </div>
          <div className="panel-body space-y-4">
            <p className="text-sm text-[var(--text-dim)]">
              El asistente aprende del criterio del equipo <strong>sin reentrenar el
              modelo</strong>: dos vías alimentan esta memoria. La{' '}
              <span className="text-hud-fg">implícita</span> se dispara sola cuando, tras
              una respuesta, seguís preguntando sobre el mismo tema. La{' '}
              <span className="text-hud-fg">explícita</span> nace del botón «Corregir» de
              cada respuesta. Los ejemplos afines se inyectan en el prompt de consultas
              futuras.
            </p>

            <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
              <div className="border border-hud-soft bg-inset/60 px-3 py-2 [clip-path:var(--chamfer-sm)]">
                <p className="hud-label">Respuestas que funcionaron</p>
                <p className="hud-data mt-1 text-lg text-emerald-400">{fb.positive}</p>
              </div>
              <div className="border border-hud-soft bg-inset/60 px-3 py-2 [clip-path:var(--chamfer-sm)]">
                <p className="hud-label">Correcciones del analista</p>
                <p className="hud-data mt-1 text-lg text-[#f0abfc]">{fb.negative}</p>
              </div>
              <div className="border border-hud-soft bg-inset/60 px-3 py-2 [clip-path:var(--chamfer-sm)]">
                <p className="hud-label">Índice semántico</p>
                <p
                  className={`hud-data mt-1 text-lg ${
                    fb.indexed ? 'text-emerald-400' : 'text-amber-400'
                  }`}
                >
                  {fb.indexed ? 'Activo' : 'Solo léxico'}
                </p>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-3">
              <button className="btn-hud" onClick={() => void onClearFeedback()} disabled={fbBusy}>
                {fbBusy ? 'Vaciando…' : 'Vaciar memoria'}
              </button>
              <span className="text-xs text-[var(--text-dim)]">
                Se usa un máximo de 2 ejemplos por consulta (la ventana de contexto es
                finita). Vaciar no afecta al modelo ni a los datos.
              </span>
            </div>

            {fb.total > 0 && (
              <p className="font-mono text-[11px] text-[var(--text-dim)]">
                Fuente legible y editable a mano: {fb.path}
              </p>
            )}
          </div>
        </div>
      )}

      {/* ------------------------------------------------------------- */}
      {/* Acciones: guardar / descartar                                 */}
      {/* ------------------------------------------------------------- */}
      <div className="flex flex-wrap items-center gap-3">
        <button
          className="btn-hud btn-primary-hud"
          onClick={() => void onSave()}
          disabled={!dirty || save === 'saving'}
        >
          {save === 'saving' ? 'Guardando…' : 'Guardar cambios'}
        </button>
        <button
          className="btn-hud"
          onClick={() => {
            setCfg(saved)
            setError(null)
            setSave('idle')
          }}
          disabled={!dirty}
        >
          Descartar
        </button>
        {save === 'saved' && (
          <span className="chip text-emerald-400" style={{ background: '#10b98114' }}>
            ✓ Guardado · aplica al instante
          </span>
        )}
        {!dirty && save === 'idle' && (
          <span className="hud-label">Sin cambios pendientes</span>
        )}
        {error && <span className="chip text-rose-400">{error}</span>}
      </div>
    </div>
  )
}

export default AiOptions
