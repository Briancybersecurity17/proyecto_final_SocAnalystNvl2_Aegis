import React from 'react'
import { useAnalysisStore } from '../store'
import { PDFDownloadLink, Page, Text, View, Document, StyleSheet } from '@react-pdf/renderer'
import { saveAs } from 'file-saver'

// Simple PDF layout – you can replace this with a richer template later
const styles = StyleSheet.create({
  page: { padding: 30 },
  section: { marginBottom: 10 },
  heading: { fontSize: 16, marginBottom: 4 },
  normal: { fontSize: 12 },
})

const ReportDoc: React.FC<{summary:any}> = ({summary}) => (
  <Document>
    <Page size="A4" style={styles.page}>
      <View style={styles.section}>
        <h3 style={styles.heading}>Resumen ejecutivo</h3>
        <Text style={styles.normal}>Riesgo: {summary.risk} – {summary.rows} filas analizadas.</Text>
      </View>
      <View style={styles.section}>
        <h3 style={styles.heading}>Detalles técnicos</h3>
        <Text style={styles.normal}>Columnas encontradas: {summary.columns.join(', ')}</Text>
      </View>
    </Page>
  </Document>
)

const ReportGenerator: React.FC = () => {
  const summary = useAnalysisStore(state => state.summary)
  const status = useAnalysisStore(state => state.status)

  if (status !== 'done' || !summary) {
    return null
  }

  return (
    <div className="mt-4">
      <PDFDownloadLink document={<ReportDoc summary={summary} />} fileName="SOC_Analysis_Report.pdf" style={{ textDecoration: 'none' }}
        children={({ blob, url, loading, error }) => {
          if (loading) return <button disabled className="bg-gray-300 text-white py-2 px-4 rounded">Generating PDF…</button>
          if (error) return <div className="text-red-600">Error generating PDF</div>
          return (
            <button
              onClick={() => {
                // Force download via file-saver for better cross‑browser support
                saveAs(blob, 'SOC_Analysis_Report.pdf')
              }}
              className="bg-primary text-white py-2 px-4 rounded hover:bg-primary/80"
            >Download PDF Report</button>
          )
        }}
      />
    </div>
  )
}

export default ReportGenerator
