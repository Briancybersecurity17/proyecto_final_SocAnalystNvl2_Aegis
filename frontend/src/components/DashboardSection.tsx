import React, { useState } from 'react';

interface DashboardSectionProps {
  /** Título opcional: algunos paneles ya lo dicen la cabecera de página. */
  title?: string;
  children: React.ReactNode;
  className?: string;
  /** Hace plegable el panel (chevron en la cabecera); solo el panel de IA lo usa. */
  collapsible?: boolean;
  /** Estado inicial; por defecto expandido (D9). */
  defaultCollapsed?: boolean;
}

/**
 * Tarjeta de sección. El hover solo cambia la sombra (sin trasladar la
 * tarjeta): varias contienen gráficos interactivos de Recharts y un
 * desplazamiento bajo el cursor provocaría parpadeo al entrar/salir del
 * hover. El lift de 2–3 px vive en las tarjetas de métricas (SummaryCard).
 *
 * Con `collapsible` la cabecera gana un botón de chevron que oculta o
 * muestra el cuerpo entero: colapsar solo lo oculta, nunca recorta ni
 * resume `children` (el texto de la IA se lee completo tal cual llega).
 */
export const DashboardSection: React.FC<DashboardSectionProps> = ({
  title,
  children,
  className,
  collapsible,
  defaultCollapsed,
}) => {
  // Cuerpo con nombre: los hooks (useState) no pueden vivir en la función
  // flecha sin nombre del render anterior.
  const [collapsed, setCollapsed] = useState(defaultCollapsed ?? false);

  return (
    <div className={`panel transition-shadow duration-200 hover:shadow-[0_0_22px_rgba(31,200,255,0.35)] ${className ?? ''}`}>
      {title && (
        <div className="panel-head">
          <h3 className="panel-title">{title}</h3>
          {collapsible && (
            <button
              type="button"
              className="ml-auto border border-hud-soft bg-inset px-1.5 text-hud-line hover:border-hud-line/60 [clip-path:var(--chamfer-sm)]"
              aria-expanded={!collapsed}
              aria-label={collapsed ? `Mostrar ${title}` : `Ocultar ${title}`}
              onClick={() => setCollapsed(prev => !prev)}
            >
              <span aria-hidden="true">{collapsed ? '▸' : '▾'}</span>
            </button>
          )}
        </div>
      )}
      {!collapsed && <div className="panel-body">{children}</div>}
    </div>
  );
};
