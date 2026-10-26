import { getAnalysis } from './api/client'
import { useAnalysisStore, useUiStore } from './store'
import { clearAnalysisRef, loadAnalysisRef } from './persist'

/**
 * Restauración del análisis tras una recarga de la pestaña (F5 o reciclage
 * de memoria del navegador).
 *
 * Solo se persiste el id: aquí se le pregunta al backend si sigue vivo y se
 * reconstruye la vista con su resumen. Casos:
 *
 *  - `done`          → se restaura el resumen y la última sección visitada.
 *  - `processing`    → se retoma el seguimiento (la sección de Carga se
 *                      encarga de volver a escuchar los eventos).
 *  - `404`           → el backend se reinició: se olvida la referencia y se
 *                      avisa para volver a subir el archivo.
 *  -  red caída      → se conserva la referencia (para reintentar) y se
 *                      avisa; no se borra nada sin confirmación.
 */
export const restoreAnalysis = async (): Promise<void> => {
  const ref = loadAnalysisRef()
  if (!ref) return

  const store = useAnalysisStore.getState()
  const setSection = useUiStore.getState().setSection

  // Mientras se consulta, la UI no debe mostrar «Sin datos todavía».
  store.setNotice('Restaurando el análisis anterior…')
  store.setStatus('processing')
  store.setAnalysisId(ref.analysisId)

  try {
    const data = await getAnalysis(ref.analysisId)

    if (data.status === 'done' && data.summary && typeof data.summary.rows === 'number') {
      useAnalysisStore.getState().setSummary(data.summary)
      setSection(ref.section)
      useAnalysisStore.getState().setNotice(
        `Análisis restaurado: ${data.summary.file_name ?? 'archivo anterior'} · ` +
          `${data.summary.rows} registros. No hace falta subirlo otra vez.`,
      )
      return
    }

    if (data.status === 'pending' || data.status === 'processing') {
      // El backend aún lo tiene en curso: se retoma el seguimiento.
      setSection('upload')
      useAnalysisStore.getState().setNotice(
        'El análisis anterior sigue en proceso: se retoma desde donde quedó.',
      )
      return
    }

    // status 'error' u otro: no hay nada que restaurar.
    throw Object.assign(new Error('analysis not restorable'), { restorable: true })
  } catch (err) {
    const status = (err as { response?: { status?: number } })?.response?.status
    const storeNow = useAnalysisStore.getState()

    if (status === 404 || (err as { restorable?: boolean })?.restorable) {
      clearAnalysisRef()
      storeNow.reset()
      useAnalysisStore.getState().setNotice(
        'El análisis anterior ya no está en el backend (¿se reinició el servicio?). ' +
          'Volvé a subir el archivo Excel para continuar.',
      )
      return
    }

    // Red caída o backend apagado: se conserva la referencia para poder
    // restaurar cuando vuelva, y se ofrece reintentar desde la banda.
    storeNow.reset()
    useAnalysisStore.getState().setNotice(
      'No se pudo contactar con el backend en el puerto 8500. ' +
        'Cuando vuelva a estar activo, usá «Restaurar» para recuperar el análisis.',
    )
    // la referencia guardada se mantiene a propósito (no se borra sin red)
  }
}

/** ¿Hay un análisis guardado que aún no se restauró? (para el botón «Restaurar») */
export const hasStoredRef = (): boolean => loadAnalysisRef() !== null
