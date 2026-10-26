import React from 'react';
import { motion } from 'framer-motion';
import { cardIn, useCountUp } from '../motion';
import { fmtCount } from '../charts';

interface SummaryCardProps {
  label: string;
  value: React.ReactNode;
}

/** Cuenta hasta el valor cuando es numérico (con miles); si no, lo pinta tal cual. */
const AnimatedValue: React.FC<{ value: number }> = ({ value }) => <>{fmtCount(useCountUp(value))}</>;

export const SummaryCard: React.FC<SummaryCardProps> = ({ label, value }) => (
  <motion.div
    variants={cardIn}
    whileHover={{ y: -3, transition: { duration: 0.18 } }}
    className="bg-white rounded shadow p-4 text-center transition-shadow duration-200 hover:shadow-md"
  >
    <h3 className="text-lg font-medium mb-1">{label}</h3>
    <p className="text-2xl font-bold">
      {typeof value === 'number' ? <AnimatedValue value={value} /> : value}
    </p>
  </motion.div>
);
