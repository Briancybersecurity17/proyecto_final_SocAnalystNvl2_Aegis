import React from 'react';

interface DashboardSectionProps {
  title: string;
  children: React.ReactNode;
  className?: string;
}

export const DashboardSection: React.FC<DashboardSectionProps> = ({ title, children, className }) => (
  <div className={`bg-white rounded shadow p-4 ${className ?? ''}`}>
    <h3 className="text-xl font-semibold mb-4">{title}</h3>
    {children}
  </div>
);
