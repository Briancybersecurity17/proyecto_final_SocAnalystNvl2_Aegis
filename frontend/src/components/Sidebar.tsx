import React, { useState } from 'react';
// import { useStore } from '../store';

type Section = 'dashboard' | 'security' | 'risk' | 'upload' | 'report';

export const Sidebar: React.FC<{ active: Section; setActive: (s: Section) => void }> = ({ active, setActive }) => {
  const [open, setOpen] = React.useState(true);

  const sections: { id: Section; label: string; icon: JSX.Element }[] = [
    { id: 'upload', label: 'Upload', icon: <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg> },
    { id: 'dashboard', label: 'Dashboard', icon: <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg> },
    { id: 'report', label: 'Report', icon: <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M4 4h16v16H4z"/><path d="M4 9h16"/></svg> },
      { id: 'security', label: 'Security', icon: <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 2l9 5v6c0 5-3 9-9 11-6-2-9-6-9-11V7l9-5z"/></svg> },
    { id: 'risk', label: 'Risk', icon: <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M10 2l2 2-2 2-2-2 2-2zM6 6l2 2-2 2-2-2 2-2zM14 6l2 2-2 2-2-2 2-2zM10 10l2 2-2 2-2-2 2-2z"/></svg> },
];

  return (
    <nav className="bg-gray-800 text-white w-64 flex-shrink-0 h-full fixed inset-y-0 left-0 transform ${open ? 'translate-x-0' : '-translate-x-full'} transition-transform duration-200 ease-in-out z-10 md:relative md:translate-x-0">
      <div className="p-4 text-2xl font-semibold border-b border-gray-700">Comunidad Dojo</div>
      <ul className="mt-4 space-y-2">
        {sections.map((s) => (
          <li key={s.id}>
            <button
              className={`flex items-center w-full px-4 py-2 hover:bg-gray-700 ${active === s.id ? 'bg-gray-700' : ''}`}
              onClick={() => {
                setActive(s.id);
                setOpen(false);
              }}
            >
              {s.icon}
              <span className="ml-3">{s.label}</span>
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
};
