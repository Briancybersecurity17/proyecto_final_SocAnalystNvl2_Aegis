import React, { useCallback, useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import {
  useAnalysisStore,
  AnalysisSummary,
  ReportNarrative,
} from '../store'
import { generateReport } from '../api/client'
import { EmptyState } from './EmptyState'
import { fmtCount, fmtPct } from '../charts'
import {
  BlobProvider,
  Document,
  Page,
  Text,
  View,
  StyleSheet,
} from '@react-pdf/renderer'
import * as FileSaver from 'file-saver'

/* ------------------------------------------------------------------ */
/* Constantes de dominio                                               */
/* ------------------------------------------------------------------ */
const LEVEL_ORDER = ['critico', 'alto', 'medio', 'bajo'] as const

const LEVEL_META: Record<string, { label: string; color: string; chip: string }> = {
  critico: { label: 'Crítico', color: '#dc2626', chip: 'bg-red-600 text-white' },
  alto: { label: 'Alto', color: '#f97316', chip: 'bg-orange-500 text-white' },
  medio: { label: 'Medio', color: '#ca8a04', chip: 'bg-yellow-400 text-yellow-900' },
  bajo: { label: 'Bajo', color: '#059669', chip: 'bg-emerald-500 text-white' },
}

const fmt = (v: unknown): string => {
  if (v === null || v === undefined || v === '') return '—'
  const s = String(v)
  return s.length > 26 ? `${s.slice(0, 26)}…` : s
}

/* ------------------------------------------------------------------ */
/* Estilos del PDF                                                     */
/* ------------------------------------------------------------------ */
const pdf = StyleSheet.create({
  page: {
    paddingTop: 46,
    paddingBottom: 52,
    paddingHorizontal: 42,
    fontSize: 9.5,
    fontFamily: 'Helvetica',
    color: '#1f2937',
  },
  coverPage: { padding: 0 },
  coverBar: { height: 120, backgroundColor: '#0f2d4f', paddingHorizontal: 42, paddingTop: 44 },
  coverBrand: { fontFamily: 'Helvetica-Bold', fontSize: 20, color: '#ffffff' },
  coverSub: { fontSize: 10, color: '#93c5fd', marginTop: 6 },
  coverBody: { paddingHorizontal: 42, paddingTop: 40 },
  coverTitle: { fontFamily: 'Helvetica-Bold', fontSize: 24, color: '#0f2d4f' },
  coverTagline: { fontSize: 11, color: '#4b5563', marginTop: 8, lineHeight: 16 },
  metaBox: {
    marginTop: 30,
    borderWidth: 1,
    borderColor: '#d1d5db',
    borderRadius: 4,
    padding: 14,
    backgroundColor: '#f9fafb',
  },
  metaRow: { flexDirection: 'row', marginBottom: 5 },
  metaLabel: { width: 130, color: '#6b7280' },
  metaValue: { fontFamily: 'Helvetica-Bold', flex: 1 },
  note: { marginTop: 24, fontSize: 8.5, color: '#6b7280', lineHeight: 13 },

  h1: {
    fontFamily: 'Helvetica-Bold',
    fontSize: 15,
    color: '#0f2d4f',
    marginTop: 20,
    marginBottom: 8,
    paddingBottom: 4,
    borderBottomWidth: 1.5,
    borderBottomColor: '#0f2d4f',
  },
  h2: { fontFamily: 'Helvetica-Bold', fontSize: 11.5, color: '#111827', marginTop: 14, marginBottom: 5 },
  h3: { fontFamily: 'Helvetica-Bold', fontSize: 10, color: '#374151', marginTop: 8, marginBottom: 3 },
  para: { fontSize: 9.5, lineHeight: 15, marginBottom: 6, textAlign: 'justify' },
  small: { fontSize: 8.5, color: '#4b5563', lineHeight: 13 },

  critTable: { marginTop: 6 },
  critHeader: { flexDirection: 'row', backgroundColor: '#0f2d4f', padding: 6 },
  critHeaderText: { color: '#ffffff', fontFamily: 'Helvetica-Bold', fontSize: 9 },
  critRow: { flexDirection: 'row', padding: 6, borderBottomWidth: 1, borderBottomColor: '#e5e7eb' },
  critCell: { fontSize: 9 },
  w80: { width: 78 },
  w60: { width: 58 },
  w50: { width: 52 },

  finding: {
    borderWidth: 1,
    borderColor: '#d1d5db',
    borderRadius: 4,
    padding: 10,
    marginTop: 8,
    marginBottom: 4,
  },
  findingHead: { flexDirection: 'row', alignItems: 'center', marginBottom: 4 },
  findingId: { fontFamily: 'Helvetica-Bold', fontSize: 10, color: '#0f2d4f', width: 46 },
  findingTitle: { fontFamily: 'Helvetica-Bold', fontSize: 10, flex: 1 },
  levelTag: {
    fontSize: 7.5,
    fontFamily: 'Helvetica-Bold',
    color: '#ffffff',
    paddingHorizontal: 5,
    paddingVertical: 2,
    borderRadius: 3,
  },
  evidence: {
    backgroundColor: '#f3f4f6',
    padding: 6,
    marginTop: 5,
    borderRadius: 3,
  },
  evidenceLine: { fontSize: 8, lineHeight: 12, color: '#374151' },
  evidenceBold: { fontFamily: 'Helvetica-Bold' },

  planRow: { flexDirection: 'row', marginTop: 7, alignItems: 'flex-start' },
  planPri: {
    width: 34,
    fontFamily: 'Helvetica-Bold',
    fontSize: 9,
    color: '#ffffff',
    backgroundColor: '#0f2d4f',
    textAlign: 'center',
    paddingVertical: 3,
    borderRadius: 3,
  },
  planBody: { flex: 1, marginLeft: 7 },
  planAction: { fontFamily: 'Helvetica-Bold', fontSize: 9.5 },
  footer: {
    position: 'absolute',
    bottom: 22,
    left: 42,
    right: 42,
    flexDirection: 'row',
    justifyContent: 'space-between',
    fontSize: 8,
    color: '#6b7280',
    borderTopWidth: 1,
    borderTopColor: '#e5e7eb',
    paddingTop: 6,
  },
})

/* ------------------------------------------------------------------ */
/* Documento PDF                                                       */
/* ------------------------------------------------------------------ */
interface DocProps {
  summary: AnalysisSummary
  report: ReportNarrative
  analysisId: string
}

const ReportDoc: React.FC<DocProps> = ({ summary, report, analysisId }) => {
  const file = summary.file_name || 'archivo importado'
  const risk = LEVEL_META[summary.risk] ?? LEVEL_META.bajo
  const generated = (report.generated_at || '').replace('T', ' ')

  const Footer: React.FC = () => (
    <View style={pdf.footer} fixed>
      <Text>Comunidad Dojo · Informe técnico SOC Analyst Nivel 2</Text>
      <Text render={({ pageNumber, totalPages }) => `Página ${pageNumber} de ${totalPages}`} />
    </View>
  )

  return (
    <Document title={`Informe SOC – ${file}`} author="Comunidad Dojo" subject="Informe técnico de seguridad">
      {/* ---------------- Portada ---------------- */}
      <Page size="A4" style={[pdf.page, pdf.coverPage]} wrap={false}>
        <View style={pdf.coverBar} fixed>
          <Text style={pdf.coverBrand}>Comunidad Dojo</Text>
          <Text style={pdf.coverSub}>SOC Analyst · Nivel 2 — Plataforma de análisis de registros</Text>
        </View>

        <View style={pdf.coverBody}>
          <Text style={pdf.coverTitle}>Informe técnico de seguridad</Text>
          <Text style={pdf.coverTagline}>
            Análisis de registros importados, identificación y correlación de eventos
            sospechosos, clasificación de riesgos y plan de acción.
          </Text>

          <View style={pdf.metaBox}>
            {[
              ['Archivo analizado', file],
              ['Hojas procesadas', (summary.sheet_names || []).join(', ') || '—'],
              ['Registros analizados', String(summary.rows)],
              ['Campos (columnas)', String(summary.columns.length)],
              ['Hosts únicos', summary.unique_hosts !== null ? String(summary.unique_hosts) : '—'],
              ['Eventos sospechosos', String(summary.suspicious_events)],
              ['Nivel de riesgo global', risk.label],
              ['Fecha de generación', generated || '—'],
              ['Fuente del informe', report.source === 'llm' ? `IA – ${report.model}` : report.model],
              ['ID de análisis', analysisId],
            ].map(([label, value]) => (
              <View key={label} style={pdf.metaRow} wrap={false}>
                <Text style={pdf.metaLabel}>{label}</Text>
                <Text style={pdf.metaValue}>{value}</Text>
              </View>
            ))}
          </View>

          <Text style={pdf.note}>
            Rigor analítico: todas las cifras, hallazgos y evidencias de este documento se
            derivan exclusivamente de los registros importados. Cada hallazgo cita su
            evidencia (Log_ID, Timestamp, Host, Event_ID y origen) para permitir la
            verificación manual. No se incorporaron datos externos a los registros
            analizados.
          </Text>
        </View>
        <Footer />
      </Page>

      {/* ---------------- Cuerpo ---------------- */}
      <Page size="A4" style={pdf.page}>
        <Footer />

        {/* 1. Resumen ejecutivo */}
        <Text style={pdf.h1}>1. Resumen ejecutivo</Text>
        <Text style={pdf.para}>{report.executive_summary}</Text>

        {/* 2. Alcance y metodología */}
        <Text style={pdf.h1}>2. Alcance y metodología</Text>
        <Text style={pdf.para}>{report.methodology}</Text>
        {summary.ai_insights && summary.ai_insights.length > 0 && (
          <View>
            <Text style={pdf.h2}>Observaciones del análisis asistido por IA</Text>
            {summary.ai_insights.map((t, i) => (
              <Text key={`ins-${i}`} style={pdf.para}>
                {`• ${t}`}
              </Text>
            ))}
          </View>
        )}
        <Text style={pdf.small}>
          Alcance: {summary.rows} registros · {summary.columns.length} campos ·{' '}
          {summary.unique_hosts ?? '—'} hosts · hojas:{' '}
          {(summary.sheet_names || []).join(', ') || '—'}
          {summary.missing_columns && summary.missing_columns.length > 0
            ? ` · columnas ausentes: ${summary.missing_columns.join(', ')}`
            : ''}
        </Text>

        {/* 3. Clasificación de criticidad */}
        <Text style={pdf.h1}>3. Clasificación de criticidad</Text>
        <Text style={pdf.para}>{summary.risk_justification}</Text>

        <View style={pdf.critTable}>
          <View style={pdf.critHeader} wrap={false}>
            <Text style={[pdf.critHeaderText, pdf.w80]}>Nivel</Text>
            <Text style={[pdf.critHeaderText, pdf.w60]}>Registros</Text>
            <Text style={[pdf.critHeaderText, pdf.w50]}>%</Text>
            <Text style={[pdf.critHeaderText, { flex: 1 }]}>Justificación</Text>
          </View>
          {LEVEL_ORDER.map((lvl) => {
            const block = summary.risk_levels?.[lvl]
            const meta = LEVEL_META[lvl]
            return (
              <View key={lvl} style={pdf.critRow} wrap={false}>
                <Text style={[pdf.critCell, pdf.w80, { fontFamily: 'Helvetica-Bold', color: meta.color }]}>
                  {meta.label}
                </Text>
                <Text style={[pdf.critCell, pdf.w60]}>{fmtCount(block?.count ?? 0)}</Text>
                <Text style={[pdf.critCell, pdf.w50]}>{fmtPct(block?.percentage ?? 0)}%</Text>
                <Text style={[pdf.critCell, { flex: 1 }]}>{block?.justification ?? '—'}</Text>
              </View>
            )
          })}
        </View>

        {/* 4. Hallazgos */}
        <Text style={pdf.h1}>4. Hallazgos, evidencias y explicación técnica</Text>
        {report.findings.map((f) => {
          const meta = LEVEL_META[f.level] ?? LEVEL_META.medio
          return (
            <View key={f.id} style={pdf.finding} wrap={false}>
              <View style={pdf.findingHead}>
                <Text style={pdf.findingId}>{f.id}</Text>
                <Text style={pdf.findingTitle}>{f.title}</Text>
                <Text style={[pdf.levelTag, { backgroundColor: meta.color }]}>
                  {meta.label.toUpperCase()}
                </Text>
              </View>
              <Text style={pdf.para}>{f.explanation}</Text>

              {f.evidence.length > 0 && (
                <View>
                  <Text style={pdf.h3}>Evidencias (registros de origen)</Text>
                  {f.evidence.map((e, i) => (
                    <View key={`${f.id}-${i}`} style={pdf.evidence} wrap={false}>
                      <Text style={pdf.evidenceLine}>
                        <Text style={pdf.evidenceBold}>#{i + 1}</Text>{'  '}
                        Log_ID {e.log_id} · {e.timestamp} · Host {e.host} · Event_ID {e.event_id} ·
                        IP {e.source_ip} · Severidad {e.severity}
                        {e.result && e.result !== '—' ? ` · Result ${e.result}` : ''}
                      </Text>
                      <Text style={pdf.evidenceLine}>Evento: {e.description}</Text>
                      <Text style={pdf.evidenceLine}>Justificación: {e.reason}</Text>
                    </View>
                  ))}
                </View>
              )}
            </View>
          )
        })}

        {/* 5. Correlación */}
        <Text style={pdf.h1}>5. Correlación de eventos</Text>
        <Text style={pdf.para}>{report.correlation}</Text>
        {summary.suspicious_by_ip.length > 0 && (
          <View style={pdf.critTable}>
            <View style={pdf.critHeader} wrap={false}>
              <Text style={[pdf.critHeaderText, { flex: 1 }]}>IP de origen</Text>
              <Text style={[pdf.critHeaderText, pdf.w60]}>Eventos</Text>
              <Text style={[pdf.critHeaderText, pdf.w60]}>Hosts</Text>
            </View>
            {summary.suspicious_by_ip.slice(0, 6).map((ip) => (
              <View key={ip.ip} style={pdf.critRow} wrap={false}>
                <Text style={[pdf.critCell, { flex: 1 }]}>{ip.ip}</Text>
                <Text style={[pdf.critCell, pdf.w60]}>{ip.events}</Text>
                <Text style={[pdf.critCell, pdf.w60]}>{ip.hosts}</Text>
              </View>
            ))}
          </View>
        )}

        {/* 6. Plan de acción */}
        <Text style={pdf.h1}>6. Plan de acción</Text>
        {report.action_plan.map((item, i) => (
          <View key={`${item.priority}-${i}`} style={pdf.planRow} wrap={false}>
            <Text style={pdf.planPri}>{item.priority}</Text>
            <View style={pdf.planBody}>
              <Text style={pdf.planAction}>{item.action}</Text>
              <Text style={pdf.small}>Fundamento: {item.rationale}</Text>
            </View>
          </View>
        ))}

        {/* 7. Conclusiones */}
        <Text style={pdf.h1}>7. Conclusiones</Text>
        <Text style={pdf.para}>{report.conclusions}</Text>
        <Text style={pdf.note}>
          Informe generado automáticamente por la plataforma Comunidad Dojo (SOC Analyst
          Nivel 2) mediante asistente de IA sobre datos calculados a partir de los
          registros importados. Verificable fila a fila desde la sección «Carga de
          archivos» de la aplicación.
        </Text>
      </Page>
    </Document>
  )
}

/* ------------------------------------------------------------------ */
/* Componente                                                          */
/* ------------------------------------------------------------------ */
const ReportGenerator: React.FC = () => {
  const summary = useAnalysisStore(state => state.summary)
  const analysisId = useAnalysisStore(state => state.analysisId)
  const status = useAnalysisStore(state => state.status)

  const [report, setReport] = useState<ReportNarrative | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const fetchReport = useCallback(async () => {
    if (!analysisId || status !== 'done') return
    setLoading(true)
    setError('')
    try {
      const narrative = await generateReport(analysisId)
      setReport(narrative)
    } catch {
      // Se limpia el informe anterior: si no, quedaba visible bajo el error,
      // como si respondiera a la petición que acaba de fallar.
      setReport(null)
      setError('No se pudo generar el informe. Reintenta.')
    } finally {
      setLoading(false)
    }
  }, [analysisId, status])

  useEffect(() => {
    fetchReport()
  }, [fetchReport])

  if (!summary || !analysisId || status !== 'done') {
    return (
      <EmptyState hint="Sube un archivo Excel y espera a que termine el análisis para generar el informe técnico." />
    )
  }

  const aiBadge =
    report?.source === 'llm'
      ? { text: `IA remota · ${report.model}`, cls: 'bg-emerald-100 text-emerald-800 border-emerald-300' }
      : { text: `IA local grounded · ${report?.model ?? ''}`, cls: 'bg-blue-100 text-blue-800 border-blue-300' }

  return (
    <section className="space-y-6">
      {/* Toolbar */}
      <div className="bg-white rounded-lg shadow p-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm text-gray-500">
            Documento estructurado: resumen, metodología, hallazgos con evidencias,
            criticidad, explicación técnica y plan de acción.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className={`text-xs px-3 py-1 rounded-full border ${aiBadge.cls}`}>
            {loading ? 'Redactando con IA…' : aiBadge.text}
          </span>
          <button
            onClick={fetchReport}
            disabled={loading}
            className="px-4 py-2 text-sm border border-gray-300 rounded hover:bg-gray-100 disabled:opacity-50"
          >
            {loading ? 'Generando…' : 'Regenerar'}
          </button>
          <BlobProvider document={report ? <ReportDoc summary={summary} report={report} analysisId={analysisId} /> : <Document><Page size="A4" /></Document>}>
            {({ blob, loading: pdfLoading, error: pdfError }) => {
              if (!report) {
                // Sin informe no hay PDF que descargar: se avisa en lugar de
                // dejar el botón en «Preparando…» indefinidamente.
                return (
                  <button disabled className="px-4 py-2 text-sm bg-gray-300 text-white rounded" title="Genera el informe para poder descargarlo">
                    Descargar informe PDF
                  </button>
                )
              }
              if (pdfLoading) {
                return (
                  <button disabled className="px-4 py-2 text-sm bg-gray-300 text-white rounded">
                    Preparando PDF…
                  </button>
                )
              }
              if (pdfError || !blob) {
                return <span className="text-sm text-red-600">Error generando el PDF</span>
              }
              return (
                <motion.button
                  onClick={() => {
                    const base = (summary.file_name ?? 'analisis').replace(/\.[^.]+$/, '')
                    FileSaver.saveAs(blob, `Informe_Seguridad_${base}.pdf`)
                  }}
                  whileTap={{ scale: 0.97 }}
                  initial={false}
                  className="group relative overflow-hidden px-4 py-2 text-sm bg-primary text-white rounded shadow hover:bg-primary/80 disabled:opacity-50"
                >
                  <span className="relative z-10">⬇ Descargar informe PDF</span>
                  <span
                    aria-hidden="true"
                    className="pointer-events-none absolute inset-y-0 -left-full w-1/2 z-0 bg-gradient-to-r from-transparent via-white/40 to-transparent transition-transform duration-700 group-hover:translate-x-[300%]"
                  />
                </motion.button>
              )
            }}
          </BlobProvider>
        </div>
      </div>

      {error && <p className="text-red-600 text-sm">{error}</p>}

      {loading && !report && (
        <div className="bg-white rounded-lg shadow p-10 text-center text-gray-500">
          <div className="animate-pulse text-4xl mb-3">🤖</div>
          <p className="font-medium">El asistente de IA está redactando el informe…</p>
          <p className="text-sm mt-1">Usa únicamente los datos calculados de tus registros.</p>
        </div>
      )}

      {report && (
        <article className="bg-white rounded-lg shadow p-6 md:p-10 max-w-4xl mx-auto border-t-4 border-primary">
          {/* Portada */}
          <header className="border-b pb-6 mb-6">
            <p className="text-xs uppercase tracking-widest text-primary font-semibold">Comunidad Dojo · SOC Analyst Nivel 2</p>
            <h1 className="text-3xl font-bold text-gray-900 mt-2">Informe técnico de seguridad</h1>
            <p className="text-gray-600 mt-2">
              Análisis de registros, correlación de eventos sospechosos, clasificación de
              riesgos y plan de acción.
            </p>
            <dl className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-5 text-sm">
              {[
                ['Archivo', summary.file_name || '—'],
                ['Registros', String(summary.rows)],
                ['Hosts', String(summary.unique_hosts ?? '—')],
                ['Riesgo global', (LEVEL_META[summary.risk] ?? LEVEL_META.bajo).label],
                ['Sospechosos', String(summary.suspicious_events)],
                ['Hojas', (summary.sheet_names || []).join(', ') || '—'],
                ['Generado', (report.generated_at || '').replace('T', ' ')],
                ['Fuente IA', report.source === 'llm' ? 'Remota' : 'Local'],
              ].map(([k, v]) => (
                <div key={k} className="bg-gray-50 rounded p-2">
                  <dt className="text-[11px] uppercase text-gray-500">{k}</dt>
                  <dd className="font-semibold text-gray-800 truncate" title={v}>{v}</dd>
                </div>
              ))}
            </dl>
          </header>

          <section className="mb-6">
            <h2 className="text-xl font-bold text-gray-900 border-b border-gray-200 pb-1 mb-2">1. Resumen ejecutivo</h2>
            <p className="text-sm leading-relaxed text-gray-700 text-justify">{report.executive_summary}</p>
          </section>

          <section className="mb-6">
            <h2 className="text-xl font-bold text-gray-900 border-b border-gray-200 pb-1 mb-2">2. Alcance y metodología</h2>
            <p className="text-sm leading-relaxed text-gray-700 text-justify">{report.methodology}</p>
            {summary.ai_insights && summary.ai_insights.length > 0 && (
              <div className="mt-3 rounded border border-blue-100 bg-blue-50/50 p-3">
                <p className="text-[11px] uppercase tracking-wide text-blue-800 font-semibold mb-2">
                  Observaciones del análisis asistido por IA
                </p>
                <ul className="space-y-1.5">
                  {summary.ai_insights.map((t, i) => (
                    <li key={i} className="text-sm text-gray-700 flex gap-2">
                      <span className="text-primary font-bold">•</span>
                      <span>{t}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </section>

          <section className="mb-6">
            <h2 className="text-xl font-bold text-gray-900 border-b border-gray-200 pb-1 mb-2">3. Clasificación de criticidad</h2>
            <p className="text-sm text-gray-700 mb-3">{summary.risk_justification}</p>
            <div className="overflow-auto">
              <table className="min-w-full text-sm border border-gray-200">
                <caption className="sr-only">Clasificación de criticidad: registros y porcentaje por nivel</caption>
                <thead className="bg-gray-800 text-white">
                  <tr>
                    <th scope="col" className="px-3 py-2 text-left">Nivel</th>
                    <th scope="col" className="px-3 py-2 text-left">Registros</th>
                    <th scope="col" className="px-3 py-2 text-left">%</th>
                    <th scope="col" className="px-3 py-2 text-left">Justificación</th>
                  </tr>
                </thead>
                <tbody>
                  {LEVEL_ORDER.map((lvl, i) => {
                    const b = summary.risk_levels?.[lvl]
                    const meta = LEVEL_META[lvl]
                    return (
                      <tr key={lvl} className={i % 2 ? 'bg-gray-50' : 'bg-white'}>
                        <td className="px-3 py-2">
                          <span className={`px-2 py-0.5 rounded text-xs font-bold ${meta.chip}`}>{meta.label}</span>
                        </td>
                        <td className="px-3 py-2 font-semibold">{fmtCount(b?.count ?? 0)}</td>
                        <td className="px-3 py-2">{fmtPct(b?.percentage ?? 0)}%</td>
                        <td className="px-3 py-2 text-gray-700">{b?.justification ?? '—'}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </section>

          <section className="mb-6">
            <h2 className="text-xl font-bold text-gray-900 border-b border-gray-200 pb-1 mb-2">4. Hallazgos, evidencias y explicación técnica</h2>
            <div className="space-y-4">
              {report.findings.map(f => {
                const meta = LEVEL_META[f.level] ?? LEVEL_META.medio
                return (
                  <div key={f.id} className="border border-gray-200 rounded p-4">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-bold text-primary">{f.id}</span>
                      <h3 className="font-semibold text-gray-900">{f.title}</h3>
                      <span className={`px-2 py-0.5 rounded text-[11px] font-bold ${meta.chip}`}>{meta.label}</span>
                    </div>
                    <p className="text-sm text-gray-700 mt-2 leading-relaxed text-justify">{f.explanation}</p>

                    {f.evidence.length > 0 && (
                      <div className="mt-3">
                        <p className="text-[11px] uppercase tracking-wide text-gray-500 font-semibold mb-1">
                          Evidencias (registros de origen)
                        </p>
                        <ul className="space-y-2">
                          {f.evidence.map((e, i) => (
                            <li key={i} className="bg-gray-50 border rounded p-2 text-xs text-gray-700">
                              <p>
                                <strong>#{i + 1}</strong> Log_ID {fmt(e.log_id)} · {fmt(e.timestamp)} · Host{' '}
                                <strong>{fmt(e.host)}</strong> · Event_ID {fmt(e.event_id)} · IP {fmt(e.source_ip)} · Severidad {fmt(e.severity)}
                                {e.result && e.result !== '—' ? <> · Result {fmt(e.result)}</> : null}
                              </p>
                              <p className="mt-1">Evento: {e.description}</p>
                              <p className="mt-1 text-gray-500">Justificación: {e.reason}</p>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </section>

          <section className="mb-6">
            <h2 className="text-xl font-bold text-gray-900 border-b border-gray-200 pb-1 mb-2">5. Correlación de eventos</h2>
            <p className="text-sm text-gray-700 text-justify mb-3">{report.correlation}</p>
            {summary.suspicious_by_ip.length > 0 && (
              <table className="min-w-full text-sm border border-gray-200">
                <caption className="sr-only">Correlación entre IP de origen, eventos sospechosos y hosts impactados</caption>
                <thead className="bg-gray-100">
                  <tr>
                    <th scope="col" className="px-3 py-2 text-left">IP de origen</th>
                    <th scope="col" className="px-3 py-2 text-left">Eventos sospechosos</th>
                    <th scope="col" className="px-3 py-2 text-left">Hosts impactados</th>
                  </tr>
                </thead>
                <tbody>
                  {summary.suspicious_by_ip.slice(0, 6).map(ip => (
                    <tr key={ip.ip}>
                      <td className="px-3 py-2 font-mono">{ip.ip}</td>
                      <td className="px-3 py-2">{fmtCount(ip.events)}</td>
                      <td className="px-3 py-2">{fmtCount(ip.hosts)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          <section className="mb-6">
            <h2 className="text-xl font-bold text-gray-900 border-b border-gray-200 pb-1 mb-2">6. Plan de acción</h2>
            <ol className="space-y-3">
              {report.action_plan.map((item, i) => (
                <li key={i} className="flex gap-3">
                  <span className="shrink-0 h-6 px-2 rounded bg-gray-800 text-white text-xs font-bold flex items-center">
                    {item.priority}
                  </span>
                  <div>
                    <p className="text-sm font-semibold text-gray-900">{item.action}</p>
                    <p className="text-xs text-gray-600 mt-0.5">Fundamento: {item.rationale}</p>
                  </div>
                </li>
              ))}
            </ol>
          </section>

          <section>
            <h2 className="text-xl font-bold text-gray-900 border-b border-gray-200 pb-1 mb-2">7. Conclusiones</h2>
            <p className="text-sm text-gray-700 text-justify">{report.conclusions}</p>
          </section>

          <footer className="mt-8 pt-4 border-t text-[11px] text-gray-500">
            Documento generado automáticamente por la plataforma Comunidad Dojo · asistente
            de IA sobre datos calculados de los registros importados. Verificable fila a
            fila desde «Carga de archivos».
          </footer>
        </article>
      )}
    </section>
  )
}

export default ReportGenerator
