import React, { useEffect } from 'react';
import { AnimatePresence, MotionConfig, motion } from 'framer-motion';
import { useAnalysisStore, useUiStore, Section } from './store';
import { loadAnalysisRef, saveAnalysisRef } from './persist';
import { restoreAnalysis } from './restore';
import Dashboard from './components/Dashboard';
import SecurityAnalysis from './components/SecurityAnalysis';
import RiskClassification from './components/RiskClassification';
import UploadSection from './components/UploadSection';
import ReportGenerator from './components/ReportGenerator';
import AssistantChat from './components/AssistantChat';
import TypingTitle from './components/TypingTitle';
import { Sidebar } from './components/Sidebar';
import BackendStatus from './components/BackendStatus';
import { sectionIn } from './motion';

const SECTION_META: Record<Section, { title: string; subtitle: string }> = {
  dashboard: {
    title: 'Dashboard',
    subtitle: 'Estadísticas y gráficos interactivos por criticidad',
  },
  security: {
    title: 'Análisis de seguridad',
    subtitle: 'Identificación de eventos sospechosos y correlación entre hosts e IPs',
  },
  assistant: {
    title: 'Asistente IA',
    subtitle: 'Consulta en lenguaje natural sobre los registros con recuperación RAG',
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

// La restauración corre una sola vez aunque React monte dos veces el árbol
// (StrictMode): duplicarla produciría dos peticiones y dos avisos.
let restoreStarted = false;

const App: React.FC = () => {
  const activeSection = useUiStore(state => state.section);
  const setActiveSection = useUiStore(state => state.setSection);
  const meta = SECTION_META[activeSection];

  const status = useAnalysisStore(state => state.status);
  const analysisId = useAnalysisStore(state => state.analysisId);
  const summary = useAnalysisStore(state => state.summary);
  const notice = useAnalysisStore(state => state.notice);
  const setNotice = useAnalysisStore(state => state.setNotice);

  // Al abrir la pestaña (F5 o reciclage de memoria del navegador) se mira si
  // quedó un análisis guardado y se le pregunta al backend si sigue ahí.
  useEffect(() => {
    if (restoreStarted) return;
    restoreStarted = true;
    if (loadAnalysisRef()) void restoreAnalysis();
  }, []);

  // Mientras hay análisis activo se recuerda cuál es y qué sección se usaba.
  useEffect(() => {
    if (status === 'done' && analysisId) saveAnalysisRef(analysisId, activeSection);
  }, [status, analysisId, activeSection]);

  // Los avisos de éxito se solan solos; los de problema esperan al analista.
  useEffect(() => {
    if (!notice || !notice.startsWith('Análisis restaurado')) return;
    const timer = window.setTimeout(() => setNotice(null), 10000);
    return () => window.clearTimeout(timer);
  }, [notice, setNotice]);

  return (
    <MotionConfig reducedMotion="user">
      <div className="flex h-screen overflow-hidden bg-gray-100">
        {/* Sidebar navigation */}
        <Sidebar active={activeSection} setActive={setActiveSection} />

        {/* Main content area */}
        <main className="flex-1 min-w-0 overflow-y-auto">
          {/* Header */}
          <header className="bg-primary text-white px-6 py-4 flex items-center justify-between shadow">
            <div>
              <TypingTitle text={meta.title} />
              <p className="text-sm text-blue-100">{meta.subtitle}</p>
            </div>
            <BackendStatus />
          </header>

          {/* Banda de avisos: restauración de análisis, backend caído, etc. */}
          <AnimatePresence>
            {notice && (
              <motion.div
                key="notice"
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: 'auto' }}
                exit={{ opacity: 0, height: 0 }}
                className="overflow-hidden"
              >
                <div
                  role="status"
                  className="mx-6 mt-4 flex flex-wrap items-center justify-between gap-3 rounded border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-900"
                >
                  <p className="flex-1 min-w-0">{notice}</p>
                  <div className="flex items-center gap-2">
                    {!summary && loadAnalysisRef() && (
                      <button
                        onClick={() => void restoreAnalysis()}
                        className="rounded bg-primary px-3 py-1.5 text-xs font-medium text-white hover:bg-primary/90 focus:outline-none focus:ring-2 focus:ring-primary"
                      >
                        Restaurar
                      </button>
                    )}
                    <button
                      onClick={() => setNotice(null)}
                      aria-label="Descartar aviso"
                      className="rounded px-2 py-1 text-blue-700 hover:bg-blue-100 focus:outline-none focus:ring-2 focus:ring-primary"
                    >
                      ✕
                    </button>
                  </div>
                </div>
              </motion.div>
            )}
          </AnimatePresence>

          {/* Section content — crossfade corto al cambiar de sección */}
          <section className="p-6">
            <AnimatePresence mode="wait" initial={false}>
              <motion.div
                key={activeSection}
                variants={sectionIn}
                initial="hidden"
                animate="show"
                exit="exit"
              >
                {activeSection === 'upload' && <UploadSection />}
                {activeSection === 'dashboard' && <Dashboard />}
                {activeSection === 'security' && <SecurityAnalysis />}
                {activeSection === 'assistant' && <AssistantChat />}
                {activeSection === 'risk' && <RiskClassification />}
                {activeSection === 'report' && <ReportGenerator />}
              </motion.div>
            </AnimatePresence>
          </section>
        </main>
      </div>
    </MotionConfig>
  );
};

export default App;
