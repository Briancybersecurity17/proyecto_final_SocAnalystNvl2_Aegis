import React, { useRef, useState } from 'react'
import { uploadExcel, getAnalysis, listenAnalysisEvents } from '../api/client'
import { useAnalysisStore } from '../store'

const UploadExcel: React.FC = () => {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [progressMsg, setProgressMsg] = useState('')
  const [fileName, setFileName] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)
  const [waitMsg, setWaitMsg] = useState('')

  const status = useAnalysisStore(state => state.status)
  const summary = useAnalysisStore(state => state.summary)
  const error = useAnalysisStore(state => state.error)
  const setAnalysisId = useAnalysisStore(state => state.setAnalysisId)
  const setStatus = useAnalysisStore(state => state.setStatus)
  const setSummary = useAnalysisStore(state => state.setSummary)
  const setError = useAnalysisStore(state => state.setError)
  const reset = useAnalysisStore(state => state.reset)

  const busy = status === 'uploading' || status === 'processing'

  /** Ruta única de importación: la usan el input y el arrastre. */
  const handleFile = async (file: File | undefined | null) => {
    if (!file) return
    if (busy) {
      setWaitMsg('Ya hay una importación en curso: espera a que termine antes de cargar otro archivo.')
      return
    }
    setWaitMsg('')
    // Se limpia el análisis anterior ANTES de validar: con un archivo no
    // válido, el resto de la app no debe seguir mostrando los datos del
    // anterior como si siguieran vigentes.
    reset()
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
      setProgressMsg('Analizando registros con el motor de IA…')
      // Escucha los eventos SSE de progreso
      const stopListening = listenAnalysisEvents(analysis_id, (state) => {
        // Solo los estados conocidos cambian la UI; cualquier otro mensaje
        // (p. ej. un estado nuevo del backend) no debe romper el flujo.
        if (state === 'pending' || state === 'processing' || state === 'done') {
          // "done" sigue en proceso hasta descargar el resumen
          setStatus('processing')
        } else if (state === 'error') {
          setStatus('error')
        }

        if (state === 'done') {
          stopListening()
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
          stopListening()
          setError('No se pudo procesar el archivo')
        }
      })
    } catch (err: any) {
      console.error(err)
      setError(err?.response?.data?.detail || 'Error al subir el archivo')
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
      className={`rounded border-2 bg-white shadow-sm p-5 transition-colors ${
        dragging ? 'border-primary bg-primary/5' : 'border-gray-200'
      }`}
    >
      <p className="text-sm text-gray-500 mb-3">
        Formato admitido: <strong>.xlsx</strong>. Se leen todas las hojas del libro.
      </p>

      <div className="flex flex-wrap items-center gap-3">
        {/* Botón propio: el input nativo queda oculto (sr-only) pero sigue
            siendo el elemento real que recibe el archivo — enfocable con
            teclado y con foco visible. */}
        <label
          className={`inline-flex items-center gap-2 rounded-lg px-5 py-2.5 text-sm font-medium text-white transition-colors cursor-pointer focus-within:ring-2 focus-within:ring-primary focus-within:ring-offset-2 ${
            busy ? 'bg-primary/60 cursor-wait' : 'bg-primary hover:bg-primary/90'
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
        <span className="text-sm text-gray-600">
          {fileName ? (
            <span className="font-medium text-gray-800">{fileName}</span>
          ) : (
            'Ningún archivo seleccionado'
          )}
        </span>
        <span className="text-sm text-gray-400">o arrastrá el archivo aquí</span>
      </div>

      {/* Estado del proceso */}
      {busy && (
        <p className="mt-3 flex items-center gap-2 text-sm text-blue-700">
          <span className="inline-block h-3.5 w-3.5 rounded-full border-2 border-blue-500 border-t-transparent animate-spin" />
          {progressMsg}
        </p>
      )}

      {waitMsg && !busy && (
        <p className="mt-3 text-sm text-amber-700" role="status">{waitMsg}</p>
      )}

      {status === 'done' && summary && !error && (
        <div className="mt-3 rounded border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
          <strong>Archivo analizado:</strong> {summary.file_name || '—'} ·{' '}
          {summary.rows} registros · {summary.unique_hosts ?? '—'} hosts ·{' '}
          {summary.columns.length} campos · hojas:{' '}
          {(summary.sheet_names || []).join(', ') || '—'}
          {summary.missing_columns && summary.missing_columns.length > 0 && (
            <span className="block text-amber-700 mt-1">
              ⚠ Columnas ausentes (no se pudieron analizar):{' '}
              {summary.missing_columns.join(', ')}
            </span>
          )}
        </div>
      )}

      {error && (
        <p className="mt-3 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </p>
      )}
    </div>
  )
}

export default UploadExcel
