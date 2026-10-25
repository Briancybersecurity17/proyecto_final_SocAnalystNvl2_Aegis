import React from 'react';
import UploadExcel from './UploadExcel';
import DataTable from './DataTable';
import { useAnalysisStore } from '../store';
import { EmptyState } from './EmptyState';

/**
 * Sección de ingesta de archivo Excel: selector de archivo + visualización
 * de los registros importados. El título de página lo pinta la cabecera.
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

      <DataTable />
    </section>
  );
};

export default UploadSection;
