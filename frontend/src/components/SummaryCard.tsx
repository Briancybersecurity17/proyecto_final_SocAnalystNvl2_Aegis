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
    className="border border-hud-soft bg-inset/80 p-4 text-center shadow-[0_0_12px_rgba(31,200,255,0.14)] transition-shadow duration-200 hover:border-hud-line/60 hover:shadow-[0_0_18px_rgba(31,200,255,0.4)] [clip-path:var(--chamfer-sm)]"
  >
    <h3 className="hud-label mb-2">{label}</h3>
    <p className="font-display text-2xl font-bold text-hud-line [text-shadow:0_0_10px_rgba(31,200,255,0.5)]">
      {typeof value === 'number' ? <AnimatedValue value={value} /> : value}
    </p>
  </motion.div>
);
