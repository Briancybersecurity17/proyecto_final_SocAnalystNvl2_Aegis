import React from 'react';
import UploadExcel from './UploadExcel';
import DataTable from './DataTable';
import { useAnalysisStore } from '../store';

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

      {/* Estado vacío: misma pieza que el resto de secciones */}
      {idle && (
        <div className="p-8 text-center text-gray-500 bg-white rounded shadow">
          <p className="text-lg font-medium mb-2">Sin datos todavía</p>
          <p>
            Seleccioná un archivo <strong>.xlsx</strong> para importarlo y ver aquí sus registros.
          </p>
        </div>
      )}

      <DataTable />
    </section>
  );
};

export default UploadSection;
