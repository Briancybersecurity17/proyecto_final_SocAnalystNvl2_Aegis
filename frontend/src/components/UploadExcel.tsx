import React, { useRef, useState } from 'react'
import { uploadExcel, getAnalysis, listenAnalysisEvents } from '../api/client'
import { useAnalysisStore } from '../store'

const UploadExcel: React.FC = () => {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [progressMsg, setProgressMsg] = useState('')

  const status = useAnalysisStore(state => state.status)
  const summary = useAnalysisStore(state => state.summary)
  const error = useAnalysisStore(state => state.error)
  const setAnalysisId = useAnalysisStore(state => state.setAnalysisId)
  const setStatus = useAnalysisStore(state => state.setStatus)
  const setSummary = useAnalysisStore(state => state.setSummary)
  const setError = useAnalysisStore(state => state.setError)
  const reset = useAnalysisStore(state => state.reset)

  const busy = status === 'uploading' || status === 'processing'

  const handleUpload = async (e: React.ChangeEvent) => {
    const files = (e.target as HTMLInputElement).files
    if (!files || files.length === 0) return
    const file = files[0]
    reset() // limpia análisis/errores de una carga anterior
    setStatus('uploading')
    setProgressMsg(`Subiendo «${file.name}»…`)
    try {
      const { analysis_id } = await uploadExcel(file)
      setAnalysisId(analysis_id)
      setStatus('processing')
      setProgressMsg('Analizando registros con el motor de IA…')
      // Escucha los eventos SSE de progreso
      const stopListening = listenAnalysisEvents(analysis_id, (state) => {
        setStatus(state as never)
        if (state === 'done') {
          stopListening()
          getAnalysis(analysis_id).then((data) => {
            setSummary(data.summary)
            setProgressMsg('Análisis completado')
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

  return (
    <div className="border rounded p-5 bg-white shadow-sm">
      <h2 className="text-lg font-semibold mb-1">Seleccionar archivo Excel</h2>
      <p className="text-sm text-gray-500 mb-3">
        Formato admitido: <strong>.xlsx</strong>. Se leen todas las hojas del libro.
      </p>

      <input
        type="file"
        accept=".xlsx"
        ref={fileInputRef}
        onChange={handleUpload}
        disabled={busy}
        className="block w-full text-sm text-gray-500 file:mr-4 file:py-2 file:px-4 file:rounded file:border-0 file:text-sm file:font-semibold file:bg-primary file:text-white hover:file:bg-primary/80 disabled:opacity-60"
      />

      {/* Estado del proceso */}
      {busy && (
        <p className="mt-3 flex items-center gap-2 text-sm text-blue-700">
          <span className="inline-block h-3.5 w-3.5 rounded-full border-2 border-blue-500 border-t-transparent animate-spin" />
          {progressMsg}
        </p>
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
