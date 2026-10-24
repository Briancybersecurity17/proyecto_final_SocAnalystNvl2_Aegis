import React from 'react';

interface SummaryCardProps {
  label: string;
  value: React.ReactNode;
}

export const SummaryCard: React.FC<SummaryCardProps> = ({ label, value }) => (
  <div className="bg-white rounded shadow p-4 text-center">
    <h3 className="text-lg font-medium mb-1">{label}</h3>
    <p className="text-2xl font-bold">{value}</p>
  </div>
);
