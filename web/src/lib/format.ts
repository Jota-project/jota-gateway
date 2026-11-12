/** Duración corta en español: «45 s», «12 min», «4 h 12 min», «3 d 4 h». */
export function formatDuration(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds))
  if (s < 60) return `${s} s`
  const minutes = Math.floor(s / 60)
  if (minutes < 60) return `${minutes} min`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) {
    const rest = minutes % 60
    return rest ? `${hours} h ${rest} min` : `${hours} h`
  }
  const days = Math.floor(hours / 24)
  const rest = hours % 24
  return rest ? `${days} d ${rest} h` : `${days} d`
}

const dateTime = new Intl.DateTimeFormat('es-ES', {
  weekday: 'long',
  day: 'numeric',
  month: 'long',
  hour: '2-digit',
  minute: '2-digit',
})

/** «jueves, 29 de octubre, 18:42» */
export function formatDateTime(iso: string): string {
  return dateTime.format(new Date(iso))
}

const MODO: Record<string, string> = { audio: 'voz', text: 'texto', status: 'avisos' }

/** «voz → voz y texto»: cómo entra y cómo sale una sesión. */
export function formatModes(input: string, outputs: string[] | null): string {
  const salida = (outputs ?? []).map((m) => MODO[m] ?? m)
  const lista = salida.length > 1 ? `${salida.slice(0, -1).join(', ')} y ${salida.at(-1)}` : (salida[0] ?? '—')
  return `${MODO[input] ?? input} → ${lista}`
}

/** Segundos con una cifra decimal y coma: 3,2 s. `null` → raya. */
export function formatSeconds(ms: number | null | undefined): string {
  return ms == null ? '—' : `${(ms / 1000).toLocaleString('es-ES', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} s`
}
