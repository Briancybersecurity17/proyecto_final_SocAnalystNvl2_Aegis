import React, { useState } from 'react';
import ServicePulse from './components/ServicePulse';
import Dashboard from './components/Dashboard';
import SecurityAnalysis from './components/SecurityAnalysis';
import RiskClassification from './components/RiskClassification';
import UploadSection from './components/UploadSection';
import ReportGenerator from './components/ReportGenerator';
import { Sidebar } from './components/Sidebar';

type Section = 'upload' | 'dashboard' | 'security' | 'risk' | 'report';

const App: React.FC = () => {
  const [activeSection, setActiveSection] = useState<Section>('dashboard');

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Sidebar navigation */}
      <Sidebar active={activeSection} setActive={setActiveSection} />

      {/* Main content area */}
      <main className="flex-1 ml-64 md:ml-0 overflow-y-auto">
        {/* Header */}
        <header className="bg-primary text-white p-4 flex items-center justify-between">
          <h1 className="text-xl font-semibold">Comunidad Dojo – SOC Analyst</h1>
          <ServicePulse />
        </header>

        {/* Section content */}
        <section className="p-4">
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
