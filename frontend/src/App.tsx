import React from 'react'
import ServicePulse from './components/ServicePulse'
import UploadExcel from './components/UploadExcel'
import Dashboard from './components/Dashboard'
import ReportGenerator from './components/ReportGenerator'

const App: React.FC = () => {
  return (
    <div className="min-h-screen flex flex-col" >
      <header className="bg-primary text-white p-4 flex items-center justify-between" >
        <h1 className="text-xl font-semibold">Comunidad Dojo – SOC Analyst</h1>
        <ServicePulse />
      </header>
      <main className="flex-1 p-4 space-y-6" >
        <section>
          <UploadExcel />
        </section>
        <section>
          <Dashboard />
        </section>
        <section>
          <ReportGenerator />
        </section>
      </main>
      <footer className="bg-gray-200 text-center p-2" >
        <p className="text-sm text-gray-600">© 2026 Comunidad Dojo – All rights reserved.</p>
      </footer>
    </div>
  )
}

export default App
