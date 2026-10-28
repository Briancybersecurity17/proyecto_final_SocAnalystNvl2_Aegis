import React from 'react';
import UploadExcel from './UploadExcel';
import UploadHistory from './UploadHistory';
import DataTable from './DataTable';
import { useAnalysisStore } from '../store';
import { EmptyState } from './EmptyState';

/**
 * Sección de ingesta de archivo Excel: drop-zone + resultado + historial.
 * Los registros se ven en el Dashboard (se mudan en el Task 5). El título
 * de página lo pinta la cabecera.
 */
const UploadSection: React.FC = () => {
  const status = useAnalysisStore(state => state.status);
  const idle = status === 'idle';

  return (
    <section className="space-y-6">
      <UploadExcel />

      {/* Estado vacío: misma pieza que el resto de secciones (sin atajo,
          porque la carga está justo encima) */}
      {idle && (
        <EmptyState
          cta={false}
          hint={<>Seleccioná un archivo <strong>.xlsx</strong> —o arrastralo aquí— para importarlo y ver sus registros.</>}
        />
      )}

      {/* Historial de subidas: siempre visible (con su estado vacío propio). */}
      <UploadHistory />

      <DataTable />
    </section>
  );
};

export default UploadSection;
