import React from 'react';

interface DashboardSectionProps {
  title: string;
  children: React.ReactNode;
  className?: string;
}

/**
 * Tarjeta de sección. El hover solo cambia la sombra (sin trasladar la
 * tarjeta): varias contienen gráficos interactivos de Recharts y un
 * desplazamiento bajo el cursor provocaría parpadeo al entrar/salir del
 * hover. El lift de 2–3 px vive en las tarjetas de métricas (SummaryCard).
 */
export const DashboardSection: React.FC<DashboardSectionProps> = ({ title, children, className }) => (
  <div className={`bg-white rounded shadow p-4 transition-shadow duration-200 hover:shadow-md ${className ?? ''}`}>
    <h3 className="text-xl font-semibold mb-4">{title}</h3>
    {children}
  </div>
);
