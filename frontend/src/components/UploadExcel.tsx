import React, { useRef, useState } from 'react'
import { uploadExcel, getAnalysis, listenAnalysisEvents } from '../api/client'
import { useAnalysisStore } from '../store'

const UploadExcel: React.FC = () => {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [progressMsg, setProgressMsg] = useState('')
  const setAnalysisId = useAnalysisStore(state => state.setAnalysisId)
  const setStatus = useAnalysisStore(state => state.setStatus)
  const setSummary = useAnalysisStore(state => state.setSummary)
  const setError = useAnalysisStore(state => state.setError)

  const handleUpload = async (e: React.ChangeEvent) => {
    const files = (e.target as HTMLInputElement).files
    if (!files || files.length === 0) return
    const file = files[0]
    setStatus('uploading')
    setProgressMsg('Uploading…')
    try {
      const { analysis_id } = await uploadExcel(file)
      setAnalysisId(analysis_id)
      setStatus('processing')
      setProgressMsg('Processing…')
      // Listen to SSE events for status updates
      const stopListening = listenAnalysisEvents(analysis_id, (status) => {
        setStatus(status as any)
        if (status === 'done') {
          stopListening()
          // fetch final result
          getAnalysis(analysis_id).then((data) => {
            setSummary(data.summary)
            setProgressMsg('Ready')
          })
        }
        if (status === 'error') {
          stopListening()
          setError('Analysis failed')
        }
      })
    } catch (err: any) {
      console.error(err)
      setError(err?.response?.data?.detail || 'Upload failed')
    }
  }

  return (
    <div className="border rounded p-4 bg-white shadow-sm">
      <h2 className="text-lg font-medium mb-2">Upload Excel Log</h2>
      <input
        type="file"
        accept=".xlsx"
        ref={fileInputRef}
        onChange={handleUpload}
        className="block w-full text-sm text-gray-500 file:mr-4 file:py-2 file:px-4 file:rounded file:border-0 file:text-sm file:font-semibold file:bg-primary file:text-white hover:file:bg-primary/80"
      />
      <p className="mt-2 text-sm text-gray-600">{progressMsg}</p>
    </div>
  )
}

export default UploadExcel
