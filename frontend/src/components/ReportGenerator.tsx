import React, { useCallback, useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import {
  useAnalysisStore,
  AnalysisSummary,
  ReportNarrative,
} from '../store'
import { generateReport, getReport } from '../api/client'
import { EmptyState } from './EmptyState'
import { fmtCount, fmtPct } from '../charts'
import {
  BlobProvider,
  Document,
  Page,
  Text,
  View,
  StyleSheet,
  Font,
} from '@react-pdf/renderer'
import * as FileSaver from 'file-saver'
import fontRegular from '../assets/fonts/JetBrainsMono-Regular.ttf'
import fontBold from '../assets/fonts/JetBrainsMono-Bold.ttf'

/* Tipografía AEGIS: JetBrains Mono embebida en el PDF.
   react-pdf NO usa las fuentes del sistema ni las de Google Fonts: hay que
   registrar el archivo (por eso el informe salía en Helvetica, ajeno a la
   app). La interfaz entera se renderiza en JetBrains Mono, así que el
   documento queda con la misma voz que la pantalla. Licencia SIL OFL. */
Font.register({
  family: 'JetBrains Mono',
  fonts: [
    { src: fontRegular, fontWeight: 400 },
    { src: fontBold, fontWeight: 700 },
  ],
})

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

// Los 4 campos de prosa que el validador puede aceptar del LLM (los mismos
// que define el backend en `_PROSE_FIELDS`): su presencia en `field_sources`
// distingue un informe 100 % IA de uno con prosa mixta.
const PROSE_FIELDS = ['executive_summary', 'methodology', 'correlation', 'conclusions']

const fmt = (v: unknown): string => {
  if (v === null || v === undefined || v === '') return '—'
  const s = String(v)
  return s.length > 26 ? `${s.slice(0, 26)}…` : s
}

/**
 * Parte un texto enumerado en un tramo por paso («1. …», «1) …», «1- …»).
 *
 * Hace falta porque `methodology` llega como UNA sola línea: el texto local lo
 * construye concatenando los pasos con espacios (ver `ai_service.py`) y la IA
 * a veces hace lo mismo. Como en HTML un <p> colapsa los saltos de línea, el
 * informe se leía «1. …2. … 3. …» pegado. Separando en cada marcador numérico
 * se renderiza un párrafo por paso, con o sin saltos de línea en origen.
 *
 * Acepta los tres separadores habituales (punto, paréntesis, guion): la IA
 * tiende a escribir «1.» y el fallback local usa «1)», así que una regex con
 * solo paréntesis dejaba el informe de la IA entero pegado.
 */
const splitSteps = (text: string): string[] =>
  text
    .split(/\s+(?=\d{1,2}[.)-]\s)/)
    .map(step => step.trim())
    .filter(Boolean)

/**
 * Parte la prosa del resumen ejecutivo en frases.
 *
 * El modelo (y el fallback local) devuelven el resumen como UN bloque: en
 * HTML un <p> colapsa los saltos de línea y el «Lectura del análisis» salía
 * entero pegado, sin jerarquía. Separando tras cada fin de oración —y en
 * cada salto de línea real, que el backend ya normaliza— cada frase va en su
 * propio párrafo, con la primera como entradilla.
 *
 * El corte exige mayúscula (o «¿», ««», «(») tras la puntuación: no se
 * admite cifra tras el punto porque rompería abreviaturas numéricas
 * («aprox. 174 eventos»); las IP y los decimales («10.10.1.1», «3.5») no
 * llevan espacio tras el punto, así que nunca se parten.
 */
const splitSentences = (text: string): string[] =>
  text
    .split(/\n+/) // los saltos de línea reales también separan frases
    .flatMap(parrafo => {
      const trozos = parrafo.split(/([.!?…]["»”]?)\s+(?=[A-ZÁÉÍÓÚÑ¿«"“(])/)
      const frases: string[] = []
      for (let i = 0; i < trozos.length; i += 2) {
        frases.push(`${trozos[i]}${trozos[i + 1] ?? ''}`)
      }
      return frases
    })
    .map(frase => frase.trim())
    .filter(Boolean)

/**
 * Pinta el resumen ejecutivo frase a frase: la primera como entradilla
 * (un poco más grande) y el resto en párrafos aparte. Compartido por la caja
 * azul «Lectura del análisis» (IA) y el párrafo normal (fallback local).
 */
const ExecutiveProse: React.FC<{ text: string }> = ({ text }) => (
  <div className="space-y-1.5">
    {splitSentences(text).map((frase, i) => (
      <p
        key={i}
        className={
          i === 0
            ? 'text-base font-medium leading-relaxed text-gray-800 text-justify'
            : 'text-sm leading-relaxed text-gray-700 text-justify'
        }
      >
        {frase}
      </p>
    ))}
  </div>
)

/* ------------------------------------------------------------------ */
/* Estilos del PDF                                                     */
/* ------------------------------------------------------------------ */
/* NOTA (react-pdf): `lineHeight` numérico NO son puntos — es un MULTIPLICADOR
   del fontSize (caja de línea = lineHeight × fontSize, ver react-pdf docs).
   Un valor "15" estira cada línea a 15 × fontSize pt → huecos gigantes,
   portada desbordada y solapes. Usar valores tipo CSS (≈1.45–1.55). */
const pdf = StyleSheet.create({
  /* Paleta AEGIS: los mismos tokens de la app (--bg-void, --bg-panel,
     --hud-line, --text). Fondo oscuro en TODA la página (decisión de diseño
     validada sobre la maqueta). react-pdf no soporta degradados ni sombras,
     así que todo va en color plano. */
  page: {
    paddingTop: 46,
    paddingBottom: 52,
    paddingHorizontal: 42,
    fontSize: 9.5,
    fontFamily: 'JetBrains Mono',
    backgroundColor: '#050212',
    color: '#dce6f5',
  },
  coverPage: { padding: 0 },
  coverBar: {
    height: 120,
    backgroundColor: '#0d0620',
    borderLeftWidth: 5,
    borderLeftColor: '#1fc8ff',
    paddingHorizontal: 37,
    paddingTop: 34,
  },
  coverBrand: { fontSize: 30, fontWeight: 700, color: '#1fc8ff', letterSpacing: 4, lineHeight: 1.2 },
  coverSub: { fontSize: 8.5, color: '#3f6b88', letterSpacing: 2.4, marginTop: 7, textTransform: 'uppercase' },
  coverRule: { width: 210, height: 1, backgroundColor: '#1fc8ff', marginTop: 12 },
  coverBody: { paddingHorizontal: 42, paddingTop: 34 },
  coverKicker: { fontSize: 8, color: '#1fc8ff', letterSpacing: 1.8, textTransform: 'uppercase' },
  coverTitle: { fontSize: 25, fontWeight: 700, color: '#f2f6ff', marginTop: 8, lineHeight: 1.2 },
  coverTagline: { fontSize: 11, color: '#7280a3', marginTop: 9, lineHeight: 1.55 },
  metaBox: {
    marginTop: 26,
    borderWidth: 1,
    borderColor: '#12456a',
    borderLeftWidth: 3,
    borderLeftColor: '#1fc8ff',
    borderRadius: 3,
    padding: 14,
    backgroundColor: '#0d0620',
  },
  metaRow: { flexDirection: 'row', marginBottom: 4 },
  metaLabel: { width: 132, fontSize: 8, color: '#3f6b88', letterSpacing: 0.6, textTransform: 'uppercase' },
  metaValue: { flex: 1, fontSize: 9, fontWeight: 700, color: '#dce6f5' },
  /* Franja de riesgo de la portada (la numérica de la maqueta). */
  riskStrip: {
    flexDirection: 'row',
    marginTop: 24,
    borderWidth: 1,
    borderColor: '#12456a',
    backgroundColor: '#0d0620',
  },
  riskCell: { flex: 1, padding: 10 },
  riskCellDiv: { borderRightWidth: 1, borderRightColor: '#12456a' },
  riskCellHi: { backgroundColor: '#1a0510', borderLeftWidth: 3, borderLeftColor: '#ef4444' },
  riskN: { fontSize: 20, fontWeight: 700, lineHeight: 1.1 },
  riskL: { fontSize: 7.5, color: '#7280a3', letterSpacing: 1, textTransform: 'uppercase', marginTop: 4 },
  note: {
    marginTop: 22,
    fontSize: 8.5,
    color: '#7280a3',
    lineHeight: 1.6,
    borderTopWidth: 1,
    borderTopColor: '#12456a',
    paddingTop: 8,
  },

  /* Encabezados de sección: numeral recuadrado en cian + regla cian. */
  h1: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: 18,
    marginBottom: 8,
    paddingBottom: 6,
    borderBottomWidth: 2,
    borderBottomColor: '#1fc8ff',
  },
  h1Num: {
    fontSize: 9,
    color: '#1fc8ff',
    backgroundColor: '#10293f',
    borderWidth: 1,
    borderColor: '#0b7fb5',
    paddingHorizontal: 5,
    paddingVertical: 2,
    letterSpacing: 0.8,
    marginRight: 9,
  },
  h1Text: { flex: 1, fontSize: 14.5, fontWeight: 700, color: '#f2f6ff' },
  h2: { fontSize: 11, fontWeight: 700, color: '#f2f6ff', marginTop: 14, marginBottom: 5 },
  h3: {
    fontSize: 8,
    fontWeight: 700,
    color: '#1fc8ff',
    letterSpacing: 1.2,
    textTransform: 'uppercase',
    marginTop: 8,
    marginBottom: 3,
  },
  para: { fontSize: 9.5, lineHeight: 1.6, marginBottom: 6, color: '#dce6f5' },
  /* Entradilla del resumen ejecutivo: primera frase, un punto más grande. */
  leadPara: { fontSize: 10.5, lineHeight: 1.55, marginBottom: 6, color: '#f2f6ff' },
  /* Tamaños de lectura unificados en 9,5 pt: metodología, tabla, plan. */
  small: { fontSize: 9.5, color: '#dce6f5', lineHeight: 1.55 },
  planWhy: { fontSize: 9.5, color: '#7280a3', lineHeight: 1.5, marginTop: 2 },

  /* Resumen ejecutivo: distribución por criticidad como barra apilada. */
  sectionLabel: {
    fontSize: 7.5,
    fontWeight: 700,
    color: '#1fc8ff',
    letterSpacing: 1.2,
    textTransform: 'uppercase',
    marginBottom: 5,
  },
  stackBar: {
    flexDirection: 'row',
    height: 10,
    borderRadius: 3,
    overflow: 'hidden',
    backgroundColor: '#141033',
  },
  stackLegend: { flexDirection: 'row', flexWrap: 'wrap', marginTop: 6 },
  legendItem: { flexDirection: 'row', alignItems: 'center', marginRight: 14, marginBottom: 3 },
  legendDot: { width: 7, height: 7, borderRadius: 2, marginRight: 4 },
  legendText: { fontSize: 7.5, color: '#7280a3' },
  /* Caja IA con borde completo: los bordes tenues se fundían con el fondo. */
  aiBox: {
    backgroundColor: '#110a2e',
    borderWidth: 1,
    borderColor: '#1e4b73',
    borderLeftWidth: 3,
    borderLeftColor: '#1fc8ff',
    padding: 9,
    marginTop: 6,
  },
  aiLabel: {
    fontSize: 7.5,
    fontWeight: 700,
    color: '#1fc8ff',
    letterSpacing: 1.2,
    textTransform: 'uppercase',
    marginBottom: 3,
  },
  aiBullet: { fontSize: 9.5, lineHeight: 1.5, color: '#bfefff', marginBottom: 3 },

  critTable: { marginTop: 6 },
  critHeader: {
    flexDirection: 'row',
    backgroundColor: '#110a2e',
    padding: 6,
    borderWidth: 1,
    borderColor: '#1e4b73',
  },
  critHeaderText: {
    fontSize: 8,
    fontWeight: 700,
    color: '#1fc8ff',
    letterSpacing: 0.6,
    textTransform: 'uppercase',
  },
  critRow: { flexDirection: 'row', padding: 6, borderBottomWidth: 1, borderBottomColor: '#1c2a4d' },
  rowAlt: { backgroundColor: '#0f0a2e' },
  critCell: { fontSize: 9.5, color: '#dce6f5' },
  levelChip: {
    alignSelf: 'flex-start',
    fontSize: 7.5,
    fontWeight: 700,
    color: '#ffffff',
    paddingHorizontal: 4,
    paddingVertical: 2,
    letterSpacing: 0.5,
    textTransform: 'uppercase',
  },
  w80: { width: 78 },
  w60: { width: 58 },
  w50: { width: 52 },

  finding: {
    borderWidth: 1,
    borderColor: '#1e4b73',
    borderLeftWidth: 3,
    borderLeftColor: '#1fc8ff',
    borderRadius: 3,
    padding: 10,
    marginTop: 8,
    marginBottom: 4,
    backgroundColor: '#100a2b',
  },
  findingHead: { flexDirection: 'row', alignItems: 'center', marginBottom: 4 },
  findingId: { width: 46, fontSize: 9.5, fontWeight: 700, color: '#1fc8ff' },
  findingTitle: { flex: 1, fontSize: 9.5, fontWeight: 700, color: '#f2f6ff' },
  levelTag: {
    fontSize: 7,
    fontWeight: 700,
    color: '#ffffff',
    paddingHorizontal: 5,
    paddingVertical: 2,
    letterSpacing: 0.5,
    textTransform: 'uppercase',
  },
  /* Evidencias: bloque tintado en cian con borde propio para que no se funda
     ni con el fondo de la página ni con la tarjeta que lo contiene. */
  evidence: {
    backgroundColor: '#041018',
    borderWidth: 1,
    borderColor: '#14466b',
    borderLeftWidth: 3,
    borderLeftColor: '#1fc8ff',
    padding: 6,
    marginTop: 5,
  },
  evidenceLine: { fontSize: 8, lineHeight: 1.6, color: '#9fd9f2' },
  evidenceBold: { fontWeight: 700, color: '#1fc8ff' },

  planRow: { flexDirection: 'row', marginTop: 7, alignItems: 'flex-start' },
  planPri: {
    width: 34,
    fontSize: 8,
    fontWeight: 700,
    color: '#ffffff',
    backgroundColor: '#0d2b45',
    textAlign: 'center',
    paddingVertical: 3,
    borderWidth: 1,
    borderColor: '#1e4b73',
    borderLeftWidth: 3,
    borderLeftColor: '#1fc8ff',
    borderRadius: 2,
    overflow: 'hidden',
  },
  planPri1: { backgroundColor: '#3d0a0a', borderColor: '#7f1d1d', borderLeftColor: '#f87171' },
  planPri2: { backgroundColor: '#3d2405', borderColor: '#78350f', borderLeftColor: '#fbbf24' },
  planBody: { flex: 1, marginLeft: 7 },
  planAction: { fontSize: 9.5, fontWeight: 700, color: '#f2f6ff', lineHeight: 1.5 },

  footer: {
    position: 'absolute',
    bottom: 22,
    left: 42,
    right: 42,
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    fontSize: 9,
    color: '#bfefff',
    borderTopWidth: 1,
    borderTopColor: '#0b7fb5',
    paddingTop: 7,
  },
  footerBrand: { fontWeight: 700, color: '#1fc8ff', letterSpacing: 1.6 },
  footerPage: { fontWeight: 700, color: '#1fc8ff', letterSpacing: 0.8 },
})

/* Encabezado de sección: numeral recuadrado + título bajo regla cian, como
   en la maqueta aprobada. `wrap={false}` evita un título huérfano al final
   de una página. */
const SecHead: React.FC<{ num: string; title: string }> = ({ num, title }) => (
  <View style={pdf.h1} wrap={false}>
    <Text style={pdf.h1Num}>{num}</Text>
    <Text style={pdf.h1Text}>{title}</Text>
  </View>
)

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
      <Text>
        <Text style={pdf.footerBrand}>AEGIS</Text>
        {' · Informe técnico de seguridad'}
      </Text>
      <Text
        style={pdf.footerPage}
        render={({ pageNumber, totalPages }) => `PÁGINA ${pageNumber} / ${totalPages}`}
      />
    </View>
  )

  /* Tarjeta de hallazgo: cabecera (ID + título + tag de nivel), prosa y
     bloques de evidencia. `wrap={false}` mantiene la tarjeta entera en una
     página: no se corta a mitad de las evidencias. */
  const Finding: React.FC<{ f: (typeof report.findings)[number] }> = ({ f }) => {
    const meta = LEVEL_META[f.level] ?? LEVEL_META.medio
    // Texto oscuro sobre los tonos claros: en naranja/amarillo/verde el
    // blanco no llega al contraste mínimo a 7 pt.
    const tagText = meta.label === 'Crítico' ? '#ffffff' : '#0b0518'
    return (
      <View style={[pdf.finding, { borderLeftColor: meta.color }]} wrap={false}>
        <View style={pdf.findingHead}>
          <Text style={pdf.findingId}>{f.id}</Text>
          <Text style={pdf.findingTitle}>{f.title}</Text>
          <Text style={[pdf.levelTag, { backgroundColor: meta.color, color: tagText }]}>
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
  }

  return (
    <Document title={`Informe técnico de seguridad – ${file}`} author="AEGIS" subject="Informe técnico de seguridad">
      {/* ---------------- Portada ---------------- */}
      {/* Portada: sin wrap={false} — con él la página se dimensiona al
          contenido (quedaba de 503 pt y el pie solapaba la nota). */}
      <Page size="A4" style={[pdf.page, pdf.coverPage]}>
        <View style={pdf.coverBar} fixed>
          <Text style={pdf.coverBrand}>AEGIS</Text>
          <Text style={pdf.coverSub}>Plataforma de análisis de seguridad</Text>
          <View style={pdf.coverRule} />
        </View>

        <View style={pdf.coverBody}>
          <Text style={pdf.coverKicker}>Informe técnico · confidencial</Text>
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

          {/* Franja de riesgo: los conteos por nivel a golpe de vista, con la
              celda del nivel global resaltada (como en la maqueta). */}
          <View style={pdf.riskStrip}>
            {[
              ...LEVEL_ORDER.map(lvl => ({
                key: lvl,
                n: fmtCount(summary.risk_levels?.[lvl]?.count ?? 0),
                label: LEVEL_META[lvl].label,
                color: LEVEL_META[lvl].color,
                hi: lvl === summary.risk,
              })),
              {
                key: 'sospechosos',
                n: fmtCount(summary.suspicious_events),
                label: 'Sospechosos',
                color: '#1fc8ff',
                hi: false,
              },
            ].map((cell, i, all) => (
              <View
                key={cell.key}
                style={[
                  pdf.riskCell,
                  i < all.length - 1 ? pdf.riskCellDiv : {},
                  cell.hi ? pdf.riskCellHi : {},
                ]}
                wrap={false}
              >
                <Text style={[pdf.riskN, { color: cell.color }]}>{cell.n}</Text>
                <Text style={pdf.riskL}>{cell.label}</Text>
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
        <SecHead num="01" title="Resumen ejecutivo" />
        {LEVEL_ORDER.some((lvl) => (summary.risk_levels?.[lvl]?.count ?? 0) > 0) && (
          <View>
            <Text style={pdf.sectionLabel}>Distribución por criticidad</Text>
            {/* Barra apilada al 100%: `flexGrow` proporcional al porcentaje de
                cada nivel, así el ancho no depende de redondeos. */}
            <View style={pdf.stackBar}>
              {LEVEL_ORDER.map((lvl) => {
                const share = summary.risk_levels?.[lvl]?.percentage ?? 0
                if (share <= 0) return null
                return (
                  <View
                    key={lvl}
                    style={{ flexGrow: share, backgroundColor: LEVEL_META[lvl].color }}
                  />
                )
              })}
            </View>
            <View style={pdf.stackLegend}>
              {LEVEL_ORDER.map((lvl) => {
                const block = summary.risk_levels?.[lvl]
                return (
                  <View key={lvl} style={pdf.legendItem}>
                    <View style={[pdf.legendDot, { backgroundColor: LEVEL_META[lvl].color }]} />
                    <Text style={pdf.legendText}>
                      {`${LEVEL_META[lvl].label} ${fmtCount(block?.count ?? 0)} (${fmtPct(block?.percentage ?? 0)}%)`}
                    </Text>
                  </View>
                )
              })}
            </View>
          </View>
        )}
        {/* La justificación del riesgo vive solo en §3 (junto a la tabla de
            niveles): en §1 se repetía tras la misma distribución (revisión #3).
            La prosa va frase a frase, con la primera como entradilla. */}
        {report.field_sources?.executive_summary === 'llm' ? (
          <View style={pdf.aiBox}>
            <Text style={pdf.aiLabel}>Lectura del análisis</Text>
            {splitSentences(report.executive_summary).map((frase, i) => (
              <Text key={`ej-${i}`} style={i === 0 ? pdf.leadPara : pdf.para}>
                {frase}
              </Text>
            ))}
          </View>
        ) : (
          splitSentences(report.executive_summary).map((frase, i) => (
            <Text key={`ej-${i}`} style={i === 0 ? pdf.leadPara : pdf.para}>
              {frase}
            </Text>
          ))
        )}

        {/* 2. Alcance y metodología */}
        <SecHead num="02" title="Alcance y metodología" />
        {splitSteps(report.methodology).map((step, i) => (
          <Text key={`met-${i}`} style={pdf.para}>
            {step}
          </Text>
        ))}
        {summary.ai_insights && summary.ai_insights.length > 0 && (
          <View style={pdf.aiBox}>
            <Text style={pdf.aiLabel}>Observaciones del análisis asistido por IA</Text>
            {summary.ai_insights.map((t, i) => (
              <Text key={`ins-${i}`} style={pdf.aiBullet}>
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
        <SecHead num="03" title="Clasificación de criticidad" />
        <Text style={pdf.para}>{summary.risk_justification}</Text>

        {/* wrap={false}: tabla atómica — evita cabecera huérfana al final
            de una página con las filas sueltas en la siguiente. */}
        <View style={pdf.critTable} wrap={false}>
          <View style={pdf.critHeader} wrap={false}>
            <Text style={[pdf.critHeaderText, pdf.w80]}>Nivel</Text>
            <Text style={[pdf.critHeaderText, pdf.w60]}>Registros</Text>
            <Text style={[pdf.critHeaderText, pdf.w50]}>%</Text>
            <Text style={[pdf.critHeaderText, { flex: 1 }]}>Justificación</Text>
          </View>
          {LEVEL_ORDER.map((lvl, i) => {
            const block = summary.risk_levels?.[lvl]
            const meta = LEVEL_META[lvl]
            // Texto oscuro sobre los tonos claros: en naranja/amarillo/verde el
            // blanco no llega al contraste mínimo a 7,5 pt.
            const tagText = meta.label === 'Crítico' ? '#ffffff' : '#0b0518'
            return (
              <View
                key={lvl}
                style={[pdf.critRow, i % 2 === 1 ? pdf.rowAlt : {}]}
                wrap={false}
              >
                <View style={[pdf.critCell, pdf.w80]}>
                  <Text style={[pdf.levelChip, { backgroundColor: meta.color, color: tagText }]}>
                    {meta.label}
                  </Text>
                </View>
                <Text style={[pdf.critCell, pdf.w60]}>{fmtCount(block?.count ?? 0)}</Text>
                <Text style={[pdf.critCell, pdf.w50]}>{fmtPct(block?.percentage ?? 0)}%</Text>
                <Text style={[pdf.critCell, { flex: 1 }]}>{block?.justification ?? '—'}</Text>
              </View>
            )
          })}
        </View>

        {/* 4. Hallazgos — el encabezado viaja con la primera tarjeta
            (View wrap={false}): si no, quedaba huérfano al final de una
            página con todas las tarjetas en la siguiente. */}
        {report.findings.length > 0 && (
          <View wrap={false}>
            <SecHead num="04" title="Hallazgos, evidencias y explicación técnica" />
            <Finding f={report.findings[0]} />
          </View>
        )}
        {report.findings.slice(1).map((f) => (
          <Finding key={f.id} f={f} />
        ))}

        {/* 5. Correlación */}
        <SecHead num="05" title="Correlación de eventos" />
        <Text style={pdf.para}>{report.correlation}</Text>
        {summary.suspicious_by_ip.length > 0 && (
          <View style={pdf.critTable} wrap={false}>
            <View style={pdf.critHeader} wrap={false}>
              <Text style={[pdf.critHeaderText, { flex: 1 }]}>IP de origen</Text>
              <Text style={[pdf.critHeaderText, pdf.w60]}>Eventos</Text>
              <Text style={[pdf.critHeaderText, pdf.w60]}>Hosts</Text>
            </View>
            {summary.suspicious_by_ip.slice(0, 6).map((ip, i) => (
              <View
                key={ip.ip}
                style={[pdf.critRow, i % 2 === 1 ? pdf.rowAlt : {}]}
                wrap={false}
              >
                <Text style={[pdf.critCell, { flex: 1, color: '#bfefff' }]}>{ip.ip}</Text>
                <Text style={[pdf.critCell, pdf.w60]}>{ip.events}</Text>
                <Text style={[pdf.critCell, pdf.w60]}>{ip.hosts}</Text>
              </View>
            ))}
          </View>
        )}

        {/* 6. Plan de acción */}
        <SecHead num="06" title="Plan de acción" />
        {report.action_plan.map((item, i) => (
          <View key={`${item.priority}-${i}`} style={pdf.planRow} wrap={false}>
            <Text
              style={[
                pdf.planPri,
                item.priority.endsWith('1') ? pdf.planPri1 : {},
                item.priority.endsWith('2') ? pdf.planPri2 : {},
              ]}
            >
              {item.priority}
            </Text>
            <View style={pdf.planBody}>
              <Text style={pdf.planAction}>{item.action}</Text>
              <Text style={pdf.planWhy}>Fundamento: {item.rationale}</Text>
            </View>
          </View>
        ))}

        {/* 7. Conclusiones */}
        <SecHead num="07" title="Conclusiones" />
        <Text style={pdf.para}>{report.conclusions}</Text>
        <Text style={pdf.note}>
          Informe generado automáticamente por la plataforma AEGIS mediante
          asistente de IA sobre datos calculados a partir de los
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
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState('')
  // true tras un GET 404: el análisis todavía no tiene informe generado.
  const [pendiente, setPendiente] = useState(false)

  /**
   * Al montar solo SE LEE el informe cacheado (spec A): visitar la pestaña
   * ya no vuelve a llamar a la IA ni regenera el documento. 404 no es un
   * error, significa «todavía no se generó».
   */
  const fetchReport = useCallback(async () => {
    if (!analysisId || status !== 'done') return
    setLoading(true)
    setError('')
    setPendiente(false)
    try {
      const narrative = await getReport(analysisId)
      setReport(narrative)
    } catch (err) {
      // Se limpia el informe anterior: si no, quedaba visible bajo el error,
      // como si respondiera a la petición que acaba de fallar.
      setReport(null)
      const codigo = (err as { response?: { status?: number } })?.response?.status
      if (codigo === 404) {
        setPendiente(true)
      } else {
        setError('No se pudo cargar el informe. Reintenta.')
      }
    } finally {
      setLoading(false)
    }
  }, [analysisId, status])

  /**
   * Genera: primera versión (sin informe) o nueva versión con
   * `?regenerate=true`. Único punto que toca la IA.
   */
  const generar = useCallback(
    async (regenerate: boolean) => {
      if (!analysisId || status !== 'done') return
      setGenerating(true)
      setError('')
      try {
        const narrative = await generateReport(analysisId, { regenerate })
        setReport(narrative)
        setPendiente(false)
      } catch {
        setReport(null)
        setError('No se pudo generar el informe. Reintenta.')
      } finally {
        setGenerating(false)
      }
    },
    [analysisId, status],
  )

  useEffect(() => {
    fetchReport()
  }, [fetchReport])

  if (!summary || !analysisId || status !== 'done') {
    return (
      <EmptyState hint="Sube un archivo Excel y espera a que termine el análisis para generar el informe técnico." />
    )
  }

  const aiBadge = (() => {
    // Sin informe (aún no generado o cargando) no hay nada que etiquetar.
    if (!report) return null
    // `source === 'llm'` significa «al menos un campo de prosa pasó el
    // validador»: sin desglosar por campo, la UI enseñaba «IA remota» y la
    // caja «Lectura del análisis» con texto LOCAL cuando solo había pasado
    // correlation (revisión funcional #2). `field_sources` da el detalle.
    const prosa = PROSE_FIELDS.filter(campo => report.field_sources?.[campo] === 'llm')
    if (report.source !== 'llm' || prosa.length === 0) {
      return { text: `IA local grounded · ${report.model ?? ''}`, color: '#1fc8ff' }
    }
    if (prosa.length === PROSE_FIELDS.length) {
      return { text: `IA remota · ${report.model}`, color: '#10b981' }
    }
    return {
      text: `IA remota parcial · ${prosa.length}/${PROSE_FIELDS.length} campos (${prosa.join(', ')}) · ${report.model}`,
      color: '#f59e0b',
    }
  })()

  // El resumen ejecutivo se muestra SIEMPRE en §1: la caja azul solo si la
  // IA lo redactó; si es el fallback local, va como párrafo normal (antes,
  // con `source === 'llm'` no se pintaba nada si solo correlation pasaba).
  const executiveFromAi = report?.field_sources?.executive_summary === 'llm'

  return (
    <section className="space-y-6">
      {/* Toolbar */}
      <div className="panel">
        <div className="panel-body flex flex-wrap items-center justify-between gap-3">
          <p className="min-w-0 max-w-xl font-mono text-[12.5px] leading-relaxed text-[var(--text-dim)]">
            Documento estructurado: resumen, metodología, hallazgos con evidencias,
            criticidad, explicación técnica y plan de acción.
          </p>
          <div className="flex flex-wrap items-center gap-2">
            {aiBadge && (
              <span
                className="chip whitespace-normal normal-case"
                style={{ color: aiBadge.color, background: `${aiBadge.color}1a` }}
              >
                {generating ? 'Redactando con IA…' : aiBadge.text}
              </span>
            )}
            {pendiente && !report ? (
              <button
                onClick={() => void generar(false)}
                disabled={generating || loading}
                className="btn-hud btn-primary-hud whitespace-nowrap disabled:opacity-50"
              >
                {generating ? 'Generando…' : 'Generar informe'}
              </button>
            ) : (
              <button
                onClick={() => void generar(true)}
                disabled={generating || loading}
                className="btn-hud whitespace-nowrap disabled:opacity-50"
                title="El informe ya está generado: esta opción crea una versión nueva"
              >
                {generating ? 'Generando…' : 'Regenerar'}
              </button>
            )}
            <BlobProvider document={report ? <ReportDoc summary={summary} report={report} analysisId={analysisId} /> : <Document><Page size="A4" /></Document>}>
              {({ blob, loading: pdfLoading, error: pdfError }) => {
                if (!report) {
                  // Sin informe no hay PDF que descargar: se avisa en lugar de
                  // dejar el botón en «Preparando…» indefinidamente.
                  return (
                    <button
                      disabled
                      className="btn-hud whitespace-nowrap"
                      title="Genera el informe para poder descargarlo"
                    >
                      Descargar informe PDF
                    </button>
                  )
                }
                if (pdfLoading) {
                  return <button disabled className="btn-hud whitespace-nowrap">Preparando PDF…</button>
                }
                if (pdfError || !blob) {
                  return <span className="font-mono text-[13px] text-[#f87171]">Error generando el PDF</span>
                }
                return (
                  <motion.button
                    onClick={() => {
                      const base = (summary.file_name ?? 'analisis').replace(/\.[^.]+$/, '')
                      FileSaver.saveAs(blob, `Informe_Seguridad_${base}.pdf`)
                    }}
                    whileTap={{ scale: 0.97 }}
                    initial={false}
                    className="btn-hud btn-primary-hud whitespace-nowrap"
                  >
                    ⬇ Descargar informe PDF
                  </motion.button>
                )
              }}
            </BlobProvider>
          </div>
        </div>
      </div>

      {error && <p className="font-mono text-[13px] text-[#f87171]">{error}</p>}

      {loading && !report && (
        <div className="panel">
          <div className="panel-body p-10 text-center">
            <div className="mb-3 animate-pulse text-4xl">📄</div>
            <p className="font-display text-base font-bold uppercase tracking-[0.14em] text-hud-line">
              Cargando el informe guardado…
            </p>
            <p className="mt-1 font-mono text-[13px] text-[var(--text-dim)]">
              El informe se genera una sola vez y después se sirve desde el backend.
            </p>
          </div>
        </div>
      )}

      {/* Sin informe todavía: se ofrece generarlo (única llamada a la IA). */}
      {pendiente && !report && !loading && !generating && (
        <div className="panel">
          <div className="panel-body p-10 text-center">
            <div className="mb-3 text-4xl">🤖</div>
            <p className="font-display text-base font-bold uppercase tracking-[0.14em] text-hud-line">
              Este análisis todavía no tiene informe
            </p>
            <p className="mt-1 font-mono text-[13px] text-[var(--text-dim)]">
              Genéralo una vez y quedarás guardado para todas las visitas posteriores.
            </p>
          </div>
        </div>
      )}

      {generating && (
        <div className="panel">
          <div className="panel-body p-10 text-center">
            <div className="mb-3 animate-pulse text-4xl">🤖</div>
            <p className="font-display text-base font-bold uppercase tracking-[0.14em] text-hud-line">
              El asistente de IA está redactando el informe…
            </p>
            <p className="mt-1 font-mono text-[13px] text-[var(--text-dim)]">
              Usa únicamente los datos calculados de tus registros.
            </p>
          </div>
        </div>
      )}

      {report && (
        <div className="panel mx-auto max-w-4xl">
        <article className="m-2 border-t-4 border-primary bg-white text-gray-900 p-6 shadow-[0_18px_40px_-16px_rgba(0,0,0,0.9)] md:p-10">
          {/* Portada */}
          <header className="border-b pb-6 mb-6">
            <p className="text-xs uppercase tracking-widest text-primary font-semibold">AEGIS · Plataforma de análisis de seguridad</p>
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
                [
                  'Fuente IA',
                  report.source === 'llm'
                    ? report.field_sources &&
                      PROSE_FIELDS.every(c => report.field_sources?.[c] === 'llm')
                      ? 'Remota'
                      : `Remota parcial (${PROSE_FIELDS.filter(c => report.field_sources?.[c] === 'llm').length}/${PROSE_FIELDS.length} campos)`
                    : 'Local',
                ],
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
            {/* La distribución sale de datos estructurados (summary.risk_levels),
                no del texto libre: así el párrafo de abajo no repite lo que la
                grilla de cabecera ya informa. */}
            {LEVEL_ORDER.some(lvl => (summary.risk_levels?.[lvl]?.count ?? 0) > 0) && (
              <div className="mb-4">
                <p className="text-[11px] uppercase tracking-wide text-gray-500 font-semibold mb-2">
                  Distribución por criticidad
                </p>
                <div className="flex h-3 w-full overflow-hidden rounded-sm bg-gray-100">
                  {LEVEL_ORDER.map(lvl => {
                    const share = summary.risk_levels?.[lvl]?.percentage ?? 0
                    if (share <= 0) return null
                    return (
                      <div
                        key={lvl}
                        className="h-full"
                        style={{ width: `${share}%`, backgroundColor: LEVEL_META[lvl].color }}
                        title={`${LEVEL_META[lvl].label}: ${fmtCount(summary.risk_levels?.[lvl]?.count ?? 0)} registros (${fmtPct(share)}%)`}
                      />
                    )
                  })}
                </div>
                <ul className="mt-2 flex flex-wrap gap-x-5 gap-y-1">
                  {LEVEL_ORDER.map(lvl => {
                    const block = summary.risk_levels?.[lvl]
                    return (
                      <li key={lvl} className="flex items-center gap-1.5 text-xs text-gray-600">
                        <span
                          className="inline-block h-2.5 w-2.5 rounded-sm"
                          style={{ backgroundColor: LEVEL_META[lvl].color }}
                        />
                        <span className="font-semibold text-gray-800">{LEVEL_META[lvl].label}</span>
                        <span>
                          {fmtCount(block?.count ?? 0)} · {fmtPct(block?.percentage ?? 0)}%
                        </span>
                      </li>
                    )
                  })}
                </ul>
              </div>
            )}

            {/* El resumen ejecutivo se pinta SIEMPRE: la caja azul «Lectura
                del análisis» solo cuando la IA lo redactó; si es el fallback
                local, va como párrafo normal (antes, con `source === 'llm'`
                no se veía nada si solo pasaba correlation — revisión #2).
                Ambos van frase a frase (primera como entradilla): el texto
                llega como un bloque y en un <p> todo quedaba pegado. */}
            {executiveFromAi ? (
              <div className="mt-3 rounded border border-blue-100 bg-blue-50/50 p-3">
                <p className="text-[11px] uppercase tracking-wide text-blue-800 font-semibold mb-1">
                  Lectura del análisis
                </p>
                <ExecutiveProse text={report.executive_summary} />
              </div>
            ) : (
              <ExecutiveProse text={report.executive_summary} />
            )}
          </section>

          <section className="mb-6">
            <h2 className="text-xl font-bold text-gray-900 border-b border-gray-200 pb-1 mb-2">2. Alcance y metodología</h2>
            <div className="space-y-1.5">
              {splitSteps(report.methodology).map((step, i) => (
                <p
                  key={i}
                  className="text-sm leading-relaxed text-gray-700 text-justify whitespace-pre-line"
                >
                  {step}
                </p>
              ))}
            </div>
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
            Documento generado automáticamente por la plataforma AEGIS · asistente
            de IA sobre datos calculados de los registros importados. Verificable fila a
            fila desde «Carga de archivos».
          </footer>
        </article>
        </div>
      )}
    </section>
  )
}

export default ReportGenerator
