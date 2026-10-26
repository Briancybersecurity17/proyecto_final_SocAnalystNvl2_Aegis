import React, { useEffect, useRef, useState } from 'react'
import { uploadExcel, getAnalysis, listenAnalysisEvents } from '../api/client'
import { useAnalysisStore } from '../store'

const UploadExcel: React.FC = () => {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [progressMsg, setProgressMsg] = useState('')
  const [fileName, setFileName] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)
  const [waitMsg, setWaitMsg] = useState('')
  // Análisis que ya tiene escucha activa (evita dos listeners del mismo id).
  const watchingRef = useRef<string | null>(null)

  const status = useAnalysisStore(state => state.status)
  const summary = useAnalysisStore(state => state.summary)
  const error = useAnalysisStore(state => state.error)
  const analysisId = useAnalysisStore(state => state.analysisId)
  const setAnalysisId = useAnalysisStore(state => state.setAnalysisId)
  const setStatus = useAnalysisStore(state => state.setStatus)
  const setSummary = useAnalysisStore(state => state.setSummary)
  const setError = useAnalysisStore(state => state.setError)
  const reset = useAnalysisStore(state => state.reset)

  const busy = status === 'uploading' || status === 'processing'

  /**
   * Sigue el progreso de un análisis en el backend (SSE con respaldo de
   * sondeo si el stream se cae) y actualiza la tienda al terminar o fallar.
   * Sirve tanto tras subir un archivo como para retomar uno ya en curso.
   */
  const watch = (analysis_id: string, runningMsg: string) => {
    if (watchingRef.current === analysis_id) return
    watchingRef.current = analysis_id
    setProgressMsg(runningMsg)
    const stop = listenAnalysisEvents(analysis_id, (state) => {
      // Solo los estados conocidos cambian la UI; cualquier otro mensaje
      // (p. ej. un estado nuevo del backend) no debe romper el flujo.
      if (state === 'pending' || state === 'processing' || state === 'done') {
        // "done" sigue en proceso hasta descargar el resumen
        setStatus('processing')
      } else if (state === 'error') {
        setStatus('error')
      }

      if (state === 'done') {
        watchingRef.current = null
        stop()
        getAnalysis(analysis_id)
          .then((data) => {
            if (!data?.summary || typeof data.summary.rows !== 'number') {
              setError('La API terminó el análisis pero no devolvió un resumen válido')
              return
            }
            setSummary(data.summary)
            setProgressMsg('Análisis completado')
          })
          .catch(() => {
            setError('El análisis terminó, pero no se pudieron recuperar sus resultados desde la API')
          })
      }
      if (state === 'error') {
        watchingRef.current = null
        stop()
        setError('No se pudo procesar el archivo')
      }
    })
  }

  // Retoma el seguimiento de un análisis que sigue en proceso sin volver a
  // subir nada: es lo que ocurre al restaurar la referencia guardada tras
  // recargar la pestaña (o si el SSE se cortó y el respaldo por sondeo
  // terminó en otro estado).
  useEffect(() => {
    if (status !== 'processing' || !analysisId || summary) return
    if (watchingRef.current === analysisId) return
    watch(analysisId, 'Retomando el seguimiento del análisis…')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, analysisId, summary])

  /** Ruta única de importación: la usan el input y el arrastre. */
  const handleFile = async (file: File | undefined | null) => {
    if (!file) return
    if (busy) {
      setWaitMsg('Ya hay una importación en curso: espera a que termine antes de cargar otro archivo.')
      return
    }
    setWaitMsg('')
    // Se limpia el análisis ANTES de validar: con un archivo no válido, el
    // resto de la app no debe seguir mostrando los datos del anterior.
    reset()
    watchingRef.current = null
    setFileName(null) // el nombre del archivo anterior ya no corresponde
    if (!file.name.toLowerCase().endsWith('.xlsx')) {
      setError('Solo se admiten archivos .xlsx')
      return
    }
    setFileName(file.name)
    setStatus('uploading')
    setProgressMsg(`Subiendo «${file.name}»…`)
    try {
      const { analysis_id } = await uploadExcel(file)
      setAnalysisId(analysis_id)
      setStatus('processing')
      watch(analysis_id, 'Analizando registros con el motor de IA…')
    } catch (err: any) {
      console.error(err)
      const detail = err?.response?.data?.detail
      setError(
        detail ||
          (err?.code === 'ECONNABORTED'
            ? 'La subida tardó demasiado: revisá el archivo e inténtalo de nuevo'
            : 'No se pudo contactar con el backend en el puerto 8500. Comprobá que el servicio esté activo.'),
      )
    } finally {
      // permite volver a elegir el mismo archivo
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const handleUpload = (e: React.ChangeEvent) => {
    const files = (e.target as HTMLInputElement).files
    if (!files || files.length === 0) return
    void handleFile(files[0])
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    void handleFile(e.dataTransfer?.files?.[0])
  }

  return (
    <div
      onDragOver={e => {
        e.preventDefault()
        if (!busy) setDragging(true)
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={handleDrop}
      className={`panel transition-shadow ${dragging ? 'shadow-[0_0_28px_rgba(31,200,255,0.6)]' : ''}`}
    >
      <div className="panel-body">
        <p className="hud-label mb-3">
          Formato admitido: <strong className="text-hud-fg">.xlsx</strong> · se leen todas las hojas del libro
        </p>

        <div className="flex flex-wrap items-center gap-3">
          {/* Botón propio: el input nativo queda oculto (sr-only) pero sigue
              siendo el elemento real que recibe el archivo — enfocable con
              teclado y con foco visible. */}
          <label
            className={`btn-hud btn-primary-hud inline-flex cursor-pointer items-center gap-2 ${
              busy ? 'cursor-wait opacity-70' : ''
            }`}
          >
            <input
              type="file"
              accept=".xlsx"
              ref={fileInputRef}
              onChange={handleUpload}
              disabled={busy}
              className="sr-only"
            />
            {busy ? 'Importando…' : 'Seleccionar archivo'}
          </label>
          <span className="font-mono text-[13px] text-[var(--text-dim)]">
            {fileName ? (
              <span className="text-hud-fg">{fileName}</span>
            ) : (
              'Ningún archivo seleccionado'
            )}
          </span>
          <span className="hud-label">o arrastrá el archivo aquí</span>
        </div>

        {/* Estado del proceso */}
        {busy && (
          <p className="mt-3 flex items-center gap-2 font-mono text-[13px] text-hud-line">
            <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-hud-line border-t-transparent" />
            {progressMsg}
          </p>
        )}

        {waitMsg && !busy && (
          <p className="mt-3 font-mono text-[13px] text-[#ffb020]" role="status">
            {waitMsg}
          </p>
        )}

        {status === 'done' && summary && !error && (
          <div className="mt-3 border border-[#10b981]/60 bg-[#10b981]/10 px-3 py-2 font-mono text-[13px] text-[#10b981]">
            <strong>Archivo analizado:</strong> {summary.file_name || '—'} ·{' '}
            {summary.rows} registros · {summary.unique_hosts ?? '—'} hosts ·{' '}
            {summary.columns.length} campos · hojas:{' '}
            {(summary.sheet_names || []).join(', ') || '—'}
            {summary.missing_columns && summary.missing_columns.length > 0 && (
              <span className="mt-1 block text-[#ffb020]">
                ⚠ Columnas ausentes (no se pudieron analizar):{' '}
                {summary.missing_columns.join(', ')}
              </span>
            )}
          </div>
        )}

        {error && (
          <p className="mt-3 border border-[#dc2626]/60 bg-[#dc2626]/10 px-3 py-2 font-mono text-[13px] text-[#f87171]">
            {error}
          </p>
        )}
      </div>
    </div>
  )
}

export default UploadExcel
