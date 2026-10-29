# Hallazgo: resumen ejecutivo del informe flojo y con cifras mal atribuidas

**Fecha:** 2026-10-29
**Estado:** decisión tomada en conversación — pendiente de ciclo (brainstorming → spec → plan) en la próxima sesión
**Decisión acordada:** **opción B — arreglar *con medida*** (fix del prompt/merge + casos `report` en el harness del subproyecto A)

---

## Contexto

Tras cerrar el subproyecto A (harness de medición RAG+LLM, 173 tests), se revisó en vivo
el **Informe técnico** de la app con el análisis activo
(`2ec90201-7af4-44fe-9dfd-31c9f19c191b`, muestra de 715 filas, riesgo Crítico,
`source: llm`, `qwen2.5:3b`, ~40 s de generación).

El usuario observó que el **resumen ejecutivo está flojo**. Verificado contra el Excel:
además de flojo, contiene un error de atribución de cifra.

## Qué se detectó (evidencia)

| # | Problema | La verdad en los datos |
|---|---|---|
| 1 | «97 registros de **Event ID 4625**» — número real, sustantivo equivocado | 97 es el conteo de **nivel crítico** (Severity `Warning` → Crítico). Los `Event_ID == 4625` reales son **102** |
| 2 | «174 eventos sospechosos» repetido 2 veces en 4 frases | redundancia |
| 3 | Omisiones: sin distribución (603 Bajo / 97 Crítico / 8 Alto / 7 Medio), sin % total, sin la historia (fuerza bruta desde la IP externa `203.0.113.74` contra 4 cuentas en DC-01, RDP + PowerShell de `198.51.100.77`, 15 errores HTTP solo en WEB-01) | — |
| 4 | Hallazgos duplicados (2× «Intento de autenticación fallido») y errores HTTP divididos en dos hallazgos etiquetados **`bajo`** | `http_errors.levels = {critico: 15}` — contradice el mapeo Warning→Crítico |
| 5 | ids de hallazgo como `EVT-00057` (Log_ID) en vez de `H-01`, `H-02`… | — |
| 6 | Conclusiones genéricas sin cifras («Es necesario implementar medidas de seguridad adicionales…») | el fallback determinista sí incluye nivel, total, breakdown y hallazgos |

Números que **sí** están bien: 174 sospechosos, 15 errores (9×500, 6×404), hosts.
O sea: el modelo no inventó cifras nuevas, **reasignó una existente al sustantivo equivocado**
— error que hoy no cubre ni el guard (solo valida `Log_ID` en `/ask`) ni el eval.

## Causa raíz (código)

1. **`backend/ai_service.py` → `_remote_narrative()` (~línea 491):** el prompt solo pide
   «redacta, JSON con estas claves, no inventes datos» y suelta el `ctx` entero.
   Sin especificación de estructura del resumen ejecutivo (cifra + unidad + hallazgo
   principal + prioridad), sin exigir ids `H-xx`, sin prohibir duplicados, sin coherencia
   con `severity_mapping`. El modelo ve los `examples` del ctx y redacta hallazgos
   *sobre esas filas de ejemplo* → de ahí los ids `EVT-…` y los duplicados.
2. **`_merge()` (~línea 537):** confía en el `id` y `level` remotos sin validar, alinea
   la evidencia **por posición** (frágil) y no verifica una sola cifra contra el ctx.
3. **El informe no tiene ningún guard de grounding** (el guard solo cubre `/ask`).
4. **Hueco medido:** la spec del subproyecto A §2 excluye el informe por diseño
   («No cubre el informe PDF ni el dashboard») — este agujero entra por ahí.

## Alcance tentativo del ciclo (a confirmar en brainstorming)

- **Fix:** prompt endurecido del informe + `_merge` validador (ids, niveles, cifras ancladas al ctx).
- **Medida:** casos `report` en el harness (cifras del `executive_summary` contrastadas
  con pandas — sin juez LLM, igual que el resto del eval). Considerar que el report se
  regenera por `POST` y tarda ~40 s: coste de corrida a tener en cuenta.
- **Precondición ya existente:** servicios 8500/5300/11434 y el harness de A.
