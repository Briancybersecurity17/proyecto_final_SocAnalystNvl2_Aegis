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
    <nav className="bg-gray-800 text-white w-64 flex-shrink-0 h-full relative shadow-lg overflow-y-auto">
      <div className="p-4 border-b border-gray-700">
        <div className="text-xl font-semibold">Comunidad Dojo</div>
        <div className="text-xs text-gray-400 mt-0.5">SOC Analyst · Nivel 2</div>
      </div>

      <ul className="mt-3 space-y-1 px-2">
        {sections.map((s) => (
          <li key={s.id}>
            <button
              className={`flex items-center w-full px-3 py-2.5 rounded transition-colors ${
                active === s.id
                  ? 'bg-primary text-white shadow'
                  : 'text-gray-300 hover:bg-gray-700 hover:text-white'
              }`}
              onClick={() => setActive(s.id)}
              aria-current={active === s.id ? 'page' : undefined}
            >
              {s.icon}
              <span className="ml-3 text-sm">{s.label}</span>
            </button>
          </li>
        ))}
      </ul>

      <div className="absolute bottom-0 w-full p-4 text-xs text-gray-500 border-t border-gray-700">
        © 2026 Comunidad Dojo
      </div>
    </nav>
  );
};

export default Sidebar;
