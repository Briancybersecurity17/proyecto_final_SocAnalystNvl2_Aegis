import React from 'react';
import type { Section } from '../store';

interface SidebarProps {
  active: Section;
  setActive: (s: Section) => void;
}

const icon = (path: React.ReactNode) => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    className="h-5 w-5 flex-shrink-0"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
    focusable="false"
  >
    {path}
  </svg>
);

const sections: { id: Section; label: string; icon: React.ReactNode }[] = [
  {
    id: 'dashboard',
    label: 'Dashboard',
    icon: icon(
      <>
        <rect x="3" y="3" width="7" height="7" />
        <rect x="14" y="3" width="7" height="7" />
        <rect x="14" y="14" width="7" height="7" />
        <rect x="3" y="14" width="7" height="7" />
      </>
    ),
  },
  {
    id: 'security',
    label: 'Análisis de seguridad',
    icon: icon(<path d="M12 2l9 5v6c0 5-3 9-9 11-6-2-9-6-9-11V7l9-5z" />),
  },
  {
    id: 'assistant',
    label: 'Asistente IA (RAG)',
    icon: icon(
      <>
        <path d="M12 3l1.9 4.6L18.5 9.5 13.9 11.4 12 16l-1.9-4.6L5.5 9.5l4.6-1.9L12 3z" />
        <path d="M18 15l.8 1.9 1.9.8-1.9.8-.8 1.9-.8-1.9-1.9-.8 1.9-.8.8-1.9z" />
      </>
    ),
  },
  {
    id: 'attack',
    label: 'Correlaciones ATT&CK',
    icon: icon(
      <>
        <circle cx="6" cy="6" r="2.5" />
        <circle cx="18" cy="6" r="2.5" />
        <circle cx="12" cy="18" r="2.5" />
        <path d="M7.6 7.9l3 7.8M16.4 7.9l-3 7.8M8.5 6h7" />
      </>
    ),
  },
  {
    id: 'ai',
    label: 'Opciones IA',
    icon: icon(
      <>
        <line x1="4" y1="21" x2="4" y2="14" />
        <line x1="4" y1="10" x2="4" y2="3" />
        <line x1="12" y1="21" x2="12" y2="12" />
        <line x1="12" y1="8" x2="12" y2="3" />
        <line x1="20" y1="21" x2="20" y2="16" />
        <line x1="20" y1="12" x2="20" y2="3" />
        <line x1="1" y1="14" x2="7" y2="14" />
        <line x1="9" y1="8" x2="15" y2="8" />
        <line x1="17" y1="16" x2="23" y2="16" />
      </>
    ),
  },
  {
    id: 'risk',
    label: 'Clasificación de riesgos',
    icon: icon(
      <>
        <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
        <line x1="12" y1="9" x2="12" y2="13" />
        <line x1="12" y1="17" x2="12.01" y2="17" />
      </>
    ),
  },
  {
    id: 'upload',
    label: 'Carga de archivos',
    icon: icon(
      <>
        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
        <polyline points="17 8 12 3 7 8" />
        <line x1="12" y1="3" x2="12" y2="15" />
      </>
    ),
  },
  {
    id: 'report',
    label: 'Informe técnico',
    icon: icon(
      <>
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
        <polyline points="14 2 14 8 20 8" />
        <line x1="16" y1="13" x2="8" y2="13" />
        <line x1="16" y1="17" x2="8" y2="17" />
      </>
    ),
  },
];

export const Sidebar: React.FC<SidebarProps> = ({ active, setActive }) => {
  return (
    <nav className="relative z-[1] w-64 flex-shrink-0 h-full overflow-y-auto border-r border-hud-soft bg-inset/85 backdrop-blur">
      <div className="border-b border-hud-soft p-5">
        <div className="font-display text-lg font-bold uppercase leading-tight tracking-[0.18em] text-hud-line [text-shadow:0_0_10px_rgba(31,200,255,0.45)]">
          Comunidad
          <br />
          Dojo
        </div>
        <div className="hud-label mt-2">SOC Analyst · Nivel 2</div>
      </div>

      <ul className="mt-4 space-y-1.5 px-3">
        {sections.map((s) => {
          const isActive = active === s.id;
          return (
            <li key={s.id}>
              <button
                className={`flex w-full items-center px-3 py-2.5 text-left transition-all [clip-path:var(--chamfer-sm)] ${
                  isActive
                    ? 'border border-hud-line/60 bg-gradient-to-r from-hud-line/20 to-hud-line/5 text-hud-line shadow-[0_0_12px_rgba(31,200,255,0.35)]'
                    : 'border border-transparent text-[var(--text-dim)] hover:border-hud-soft hover:bg-hud-line/10 hover:text-hud-fg'
                }`}
                onClick={() => setActive(s.id)}
                aria-current={isActive ? 'page' : undefined}
              >
                {s.icon}
                <span className="ml-3 font-mono text-[13px] tracking-wide">{s.label}</span>
              </button>
            </li>
          );
        })}
      </ul>

      <div className="hud-label absolute bottom-0 w-full border-t border-hud-soft p-4">
        © 2026 Comunidad Dojo
      </div>
    </nav>
  );
};

export default Sidebar;
