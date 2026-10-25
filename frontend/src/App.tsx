import React, { useState } from 'react';
import ServicePulse from './components/ServicePulse';
import Dashboard from './components/Dashboard';
import SecurityAnalysis from './components/SecurityAnalysis';
import RiskClassification from './components/RiskClassification';
import UploadSection from './components/UploadSection';
import ReportGenerator from './components/ReportGenerator';
import { Sidebar } from './components/Sidebar';

type Section = 'upload' | 'dashboard' | 'security' | 'risk' | 'report';

const SECTION_META: Record<Section, { title: string; subtitle: string }> = {
  dashboard: {
    title: 'Dashboard',
    subtitle: 'Estadísticas y gráficos interactivos por criticidad',
  },
  security: {
    title: 'Análisis de seguridad',
    subtitle: 'Identificación de eventos sospechosos y correlación entre hosts e IPs',
  },
  risk: {
    title: 'Clasificación de riesgos',
    subtitle: 'Registros clasificados por criticidad con justificación',
  },
  upload: {
    title: 'Carga de archivos',
    subtitle: 'Importación y visualización del archivo Excel (.xlsx)',
  },
  report: {
    title: 'Informe técnico',
    subtitle: 'Documento estructurado y exportación en PDF',
  },
};

const App: React.FC = () => {
  const [activeSection, setActiveSection] = useState<Section>('dashboard');
  const meta = SECTION_META[activeSection];

  return (
    <div className="flex h-screen overflow-hidden bg-gray-100">
      {/* Sidebar navigation */}
      <Sidebar active={activeSection} setActive={setActiveSection} />

      {/* Main content area */}
      <main className="flex-1 min-w-0 overflow-y-auto">
        {/* Header */}
        <header className="bg-primary text-white px-6 py-4 flex items-center justify-between shadow">
          <div>
            <h1 className="text-xl font-semibold">{meta.title}</h1>
            <p className="text-sm text-blue-100">{meta.subtitle}</p>
          </div>
          <div className="flex items-center gap-4">
            <span
              className="hidden sm:inline-flex items-center gap-1.5 rounded-full bg-white/15 px-3 py-1 text-xs font-medium text-white"
              title="Motor de análisis y asistente de redacción del informe"
            >
              <span className="h-2 w-2 rounded-full bg-emerald-300" />
              IA integrada · análisis + informe
            </span>
            <ServicePulse />
          </div>
        </header>

        {/* Section content */}
        <section className="p-6">
          {activeSection === 'upload' && <UploadSection />}
          {activeSection === 'dashboard' && <Dashboard />}
          {activeSection === 'security' && <SecurityAnalysis />}
          {activeSection === 'risk' && <RiskClassification />}
          {activeSection === 'report' && <ReportGenerator />}
        </section>
      </main>
    </div>
  );
};

export default App;
