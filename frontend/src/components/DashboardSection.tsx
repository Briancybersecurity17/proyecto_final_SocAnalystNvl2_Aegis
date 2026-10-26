import React from 'react';

interface DashboardSectionProps {
  /** Título opcional: algunos paneles ya lo dicen la cabecera de página. */
  title?: string;
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
  <div className={`panel transition-shadow duration-200 hover:shadow-[0_0_22px_rgba(31,200,255,0.35)] ${className ?? ''}`}>
    {title && (
      <div className="panel-head">
        <h3 className="panel-title">{title}</h3>
      </div>
    )}
    <div className="panel-body">{children}</div>
  </div>
);
