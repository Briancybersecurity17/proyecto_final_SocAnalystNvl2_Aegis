import React from 'react'
import { motion } from 'framer-motion'

const ServicePulse: React.FC = () => {
  return (
    <motion.svg
      width="40"
      height="40"
      viewBox="0 0 40 40"
      initial="rest"
      animate="pulse"
    >
      <motion.path
        d="M5 20 Q20 0 35 20"
        stroke="#ff4081"
        strokeWidth="4"
        fill="transparent"
        strokeLinecap="round"
        variants={{
          rest: { opacity: 0.6 },
          pulse: {
            opacity: [0.6, 1, 0.6],
            transition: { repeat: Infinity, duration: 2, ease: 'easeInOut' },
          },
        }}
      />
    </motion.svg>
  )
}

export default ServicePulse
