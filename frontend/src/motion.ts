/**
 * motion.ts
 * ---------
 * Utilidades compartidas de animación (Framer Motion) para toda la UI.
 *
 * Principios:
 *  - Sutil: desplazamientos de 8–14 px y duraciones de 0.2–0.3 s. Nada rebota.
 *  - Accesible: `usePrefersReducedMotion()` desactiva el conteo animado y
 *    `MotionConfig reducedMotion="user"` (usado en App.tsx) anula los
 *    transform cuando el sistema operativo pide menos movimiento.
 *  - Barato: los contadores usan requestAnimationFrame y se cancelan al
 *    desmontar; las listas largas animan como máximo las primeras filas.
 */

import { useEffect, useRef, useState } from 'react'
import type { Variants } from 'framer-motion'

/** True si el SO pide reducir el movimiento (accesibilidad). */
export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState<boolean>(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  })

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)')
    const onChange = () => setReduced(mq.matches)
    mq.addEventListener?.('change', onChange)
    return () => mq.removeEventListener?.('change', onChange)
  }, [])

  return reduced
}

/** Entrada estándar: fade + 12 px hacia arriba, sin rebote. */
export const fadeInUp: Variants = {
  hidden: { opacity: 0, y: 12 },
  show: { opacity: 1, y: 0, transition: { duration: 0.28, ease: 'easeOut' } },
}

/** Entrada de una tarjeta dentro de una rejilla (más compacta). */
export const cardIn: Variants = {
  hidden: { opacity: 0, y: 10 },
  show: { opacity: 1, y: 0, transition: { duration: 0.26, ease: 'easeOut' } },
}

/** Transición de sección: entra desde 14 px, sale rápido y por debajo. */
export const sectionIn: Variants = {
  hidden: { opacity: 0, y: 14 },
  show: { opacity: 1, y: 0, transition: { duration: 0.22, ease: 'easeOut' } },
  exit: { opacity: 0, y: -8, transition: { duration: 0.14, ease: 'easeIn' } },
}

/**
 * Contenedor con entrada escalonada de sus hijos.
 * Uso: `<motion.div variants={stagger(0.06)} initial="hidden" animate="show">`
 */
export const stagger = (delay = 0.06, startAt = 0): Variants => ({
  hidden: {},
  show: { transition: { staggerChildren: delay, delayChildren: startAt } },
})

/**
 * Cuenta desde 0 hasta `target` con easeOutCubic.
 * Devuelve `target` directamente si se pidió reducir el movimiento.
 */
export function useCountUp(target: number, duration = 800): number {
  const reduced = usePrefersReducedMotion()
  const [value, setValue] = useState<number>(() =>
    reduced || !Number.isFinite(target) ? target : 0,
  )
  const frameRef = useRef<number | null>(null)

  useEffect(() => {
    if (reduced || !Number.isFinite(target)) {
      setValue(target)
      return
    }
    const startedAt = performance.now()
    const tick = (now: number) => {
      const progress = Math.min(1, (now - startedAt) / duration)
      const eased = 1 - Math.pow(1 - progress, 3) // easeOutCubic
      setValue(Math.round(target * eased))
      if (progress < 1) frameRef.current = requestAnimationFrame(tick)
    }
    frameRef.current = requestAnimationFrame(tick)
    return () => {
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current)
    }
  }, [target, duration, reduced])

  return value
}
