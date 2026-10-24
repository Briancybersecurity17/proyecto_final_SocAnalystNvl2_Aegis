import React from 'react';
import UploadExcel from './UploadExcel';
import ServicePulse from './ServicePulse';

/**
 * Sección de ingesta de archivo Excel.
 * Incluye el componente UploadExcel y el visualizador ServicePulse.
 */
const UploadSection: React.FC = () => {
  return (
    <section className="space-y-6 p-4">
      <h2 className="text-2xl font-semibold">Ingesta de archivo Excel</h2>
      <UploadExcel />
      <div className="flex items-center space-x-2">
        <ServicePulse />
        <span className="text-sm text-gray-600">Servicio activo</span>
      </div>
    </section>
  );
};

export default UploadSection;
