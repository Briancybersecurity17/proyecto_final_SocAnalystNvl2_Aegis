/* Los assets de fuente se importan como URL para que react-pdf pueda
   buscarlos y embeberlos en el PDF (Vite los copia a dist con hash). */
declare module '*.ttf' {
  const src: string
  export default src
}
