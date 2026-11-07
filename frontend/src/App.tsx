import React, { Suspense, useEffect, useRef } from 'react';
import { AnimatePresence, MotionConfig, motion } from 'framer-motion';
import { useAnalysisStore, useUiStore, Section } from './store';
import { useFilterStore, selectIsFiltered } from './store/filters';
import { loadAnalysisRef, saveAnalysisRef } from './persist';
import { restoreAnalysis } from './restore';
import TypingTitle from './components/TypingTitle';
import { Sidebar } from './components/Sidebar';
import BackendStatus from './components/BackendStatus';
import { NotesFab } from './components/NotesFab';
import { sectionIn } from './motion';

// Cada sección es un chunk aparte: la app arranca con el layout y solo baja el
// módulo que el analista abre (react-pdf vive solo en «Informe técnico» y
// recharts en los paneles con gráficos). Sin esto el bundle de entrada era de
// 2.2 MB.
const Dashboard = React.lazy(() => import('./components/Dashboard'));
const SecurityAnalysis = React.lazy(() => import('./components/SecurityAnalysis'));
const RiskClassification = React.lazy(() => import('./components/RiskClassification'));
const UploadSection = React.lazy(() => import('./components/UploadSection'));
const ReportGenerator = React.lazy(() => import('./components/ReportGenerator'));
const AssistantChat = React.lazy(() => import('./components/AssistantChat'));
const AttackCorrelationsPanel = React.lazy(
  () => import('./components/AttackCorrelations'),
);
const AttackCandidates = React.lazy(() => import('./components/AttackCandidates'));
const AiOptions = React.lazy(() => import('./components/AiOptions'));

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
  attack: {
    title: 'Correlaciones ATT&CK',
    subtitle:
      'Técnicas MITRE ancladas a los Event_ID observados y fichas candidatas con aprobación humana',
  },
  ai: {
    title: 'Opciones IA',
    subtitle: 'Proveedor cloud (compatible OpenAI), modelo y parámetros de generación',
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

/** Mientras Vite trae el chunk de la sección (solo la primera visita). */
const SectionLoading: React.FC = () => (
  <p className="hud-label animate-pulse py-12 text-center">Cargando sección…</p>
);

const App: React.FC = () => {
  const activeSection = useUiStore(state => state.section);
  const setActiveSection = useUiStore(state => state.setSection);
  const meta = SECTION_META[activeSection];

  const status = useAnalysisStore(state => state.status);
  const analysisId = useAnalysisStore(state => state.analysisId);
  const summary = useAnalysisStore(state => state.summary);
  const notice = useAnalysisStore(state => state.notice);
  const setNotice = useAnalysisStore(state => state.setNotice);

  // Tono del aviso: verde para lo que salió bien, ámbar para lo que pide
  // atención del analista y cian mientras se está restaurando.
  const noticeTone = !notice
    ? '#1fc8ff'
    : notice.startsWith('Análisis restaurado')
      ? '#10b981'
      : notice.startsWith('Restaurando')
        ? '#1fc8ff'
        : '#ffb020';

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

  // Dueño único del reset de filtros por cambio de análisis: App siempre está
  // montado, a diferencia del Dashboard/DataTable (secciones con render
  // condicional) cuyo reset no corría al reabrir o subir un archivo con esas
  // secciones desmontadas. Los resets internos de esos componentes quedan como
  // refuerzo idempotente. El ref arranca con el id del primer render para no
  // borrar filtros frescos al montar (React 18 monta dos veces en StrictMode).
  const prevResetAnalysisId = useRef<string | null>(analysisId);
  useEffect(() => {
    if (prevResetAnalysisId.current === analysisId) return;
    prevResetAnalysisId.current = analysisId;
    useFilterStore.getState().reset();
  }, [analysisId]);

  // Tecla Esc: salir de la selección/filtro activo por teclado (misma
  // acción que «Limpiar filtros»: clearAll avanza clearEpoch y también
  // invalida el debounce de la búsqueda). Si el foco está en un campo
  // editable se respeta: mientras se escribe, Esc no vacía la pantalla
  // entera; con el foco suelto, Esc vuelve a filtros neutros.
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      const target = e.target as HTMLElement | null;
      const editable =
        !!target &&
        (target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.tagName === 'SELECT' ||
          target.isContentEditable);
      if (editable) return;
      const store = useFilterStore.getState();
      if (!selectIsFiltered(store)) return;
      store.clearAll();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  // Los avisos de éxito se solan solos; los de problema esperan al analista.
  useEffect(() => {
    if (!notice || !notice.startsWith('Análisis restaurado')) return;
    const timer = window.setTimeout(() => setNotice(null), 10000);
    return () => window.clearTimeout(timer);
  }, [notice, setNotice]);

  return (
    <MotionConfig reducedMotion="user">
      <div className="flex h-screen overflow-hidden">
        {/* Grilla ambiental del HUD (fijo, no intercepta el ratón) */}
        <div className="grid-bg" aria-hidden="true" />

        {/* Sidebar navigation */}
        <Sidebar active={activeSection} setActive={setActiveSection} />

        {/* Main content area */}
        <main className="relative z-[1] flex-1 min-w-0 overflow-y-auto">
          {/* Header */}
          <header className="mx-6 mt-6 flex flex-wrap items-center justify-between gap-4">
            <div className="min-w-0">
              <TypingTitle text={meta.title} />
              <p className="hud-label mt-1 text-[var(--hud-muted)]">{meta.subtitle}</p>
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
                  className="mx-6 mt-5 flex flex-wrap items-center justify-between gap-3 border bg-inset/90 px-4 py-3 font-mono text-[13px] shadow-[0_0_14px_rgba(31,200,255,0.2)] [clip-path:var(--chamfer-sm)]"
                  style={{
                    color: noticeTone,
                    borderColor: `${noticeTone}80`,
                    boxShadow: `0 0 14px ${noticeTone}33`,
                  }}
                >
                  <p className="min-w-0 flex-1">{notice}</p>
                  <div className="flex items-center gap-2">
                    {!summary && loadAnalysisRef() && (
                      <button
                        onClick={() => void restoreAnalysis()}
                        className="btn-hud btn-primary-hud"
                      >
                        Restaurar
                      </button>
                    )}
                    <button
                      onClick={() => setNotice(null)}
                      aria-label="Descartar aviso"
                      className="rounded px-2 py-1 hover:bg-hud-line/15 focus:outline-none focus:ring-2 focus:ring-hud-line"
                      style={{ color: noticeTone }}
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
                {/* Solo la primera visita a cada sección espera su chunk. */}
                <Suspense fallback={<SectionLoading />}>
                  {activeSection === 'upload' && <UploadSection />}
                  {activeSection === 'dashboard' && <Dashboard />}
                  {activeSection === 'security' && <SecurityAnalysis />}
                  {activeSection === 'assistant' && <AssistantChat />}
                  {activeSection === 'attack' && (
                    <>
                      <div className="space-y-6">
                        <AttackCorrelationsPanel />
                      </div>
                      <div className="mt-6">
                        <AttackCandidates />
                      </div>
                    </>
                  )}
                  {activeSection === 'ai' && <AiOptions />}
                  {activeSection === 'risk' && <RiskClassification />}
                  {activeSection === 'report' && <ReportGenerator />}
                </Suspense>
              </motion.div>
            </AnimatePresence>
          </section>
        </main>

        {/* Bloc de notas flotante: fuera de <main> para flotar sobre
            cualquier sección (Correlaciones, tabla de registros, etc.) */}
        <NotesFab />
      </div>
    </MotionConfig>
  );
};

export default App;
