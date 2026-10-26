import React, { useEffect, useMemo, useRef, useState } from 'react';
import { usePrefersReducedMotion } from '../motion';

interface TypingTitleProps {
  text: string;
  /** Duración total objetivo del tipeo (ms). */
  budget?: number;
}

/**
 * Título del header con efecto terminal: escribe el texto carácter a
 * carácter al cambiar de sección y muestra un cursor mientras escribe.
 *
 * - La velocidad se adapta al largo del texto para no pasar nunca del
 *   presupuesto (por defecto 700 ms): «Dashboard» es rápido,
 *   «Clasificación de riesgos» no se queda escribiendo a mitad de la demo.
 * - Con `prefers-reduced-motion` el texto aparece completo, sin tipeo.
 */
export const TypingTitle: React.FC<TypingTitleProps> = ({ text, budget = 700 }) => {
  const reduced = usePrefersReducedMotion();
  const [shown, setShown] = useState(text);
  const [done, setDone] = useState(true);
  const firstRender = useRef(true);

  const speed = useMemo(
    () => Math.max(24, Math.min(55, budget / Math.max(1, text.length))),
    [budget, text],
  );

  useEffect(() => {
    // Solo tipea al CAMBIAR de sección: la primera carga pinta el título ya.
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    if (reduced) {
      setShown(text);
      setDone(true);
      return;
    }
    setShown('');
    setDone(false);
    let index = 0;
    const timer = window.setInterval(() => {
      index += 1;
      setShown(text.slice(0, index));
      if (index >= text.length) {
        window.clearInterval(timer);
        setDone(true);
      }
    }, speed);
    return () => window.clearInterval(timer);
  }, [text, speed, reduced]);

  return (
    <h1
      className="font-display text-2xl font-bold uppercase tracking-[0.18em] text-hud-line [text-shadow:0_0_12px_rgba(31,200,255,0.5)]"
      aria-label={text}
    >
      <span aria-hidden="true">{shown}</span>
      {!done && (
        <span
          aria-hidden="true"
          className="ml-0.5 inline-block h-5 w-[4px] translate-y-[3px] bg-hud-line animate-pulse"
        />
      )}
    </h1>
  );
};

export default TypingTitle;
