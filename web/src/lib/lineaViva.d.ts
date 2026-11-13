export type LineaVivaState = 'rest' | 'listen' | 'think' | 'tool' | 'speak'
export interface LineaViva {
  setState(state: LineaVivaState): void
  destroy(): void
}
export function createLineaViva(canvas: HTMLCanvasElement, options?: { reduce?: boolean; color?: string }): LineaViva
