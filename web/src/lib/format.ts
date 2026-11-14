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

/**
 * Fecha del gateway. Una hora sin zona (`2026-11-14T18:42:00`) es UTC, que es como las guarda el
 * gateway; `new Date()` la tomaría por hora local y la correría el desfase del navegador.
 */
export function parseIso(iso: string): Date {
  return new Date(/T\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(iso) ? `${iso}Z` : iso)
}

/** «jueves, 29 de octubre, 18:42» */
export function formatDateTime(iso: string): string {
  return dateTime.format(parseIso(iso))
}

const MODO: Record<string, string> = { audio: 'voz', text: 'texto', status: 'avisos' }

/** «voz → voz y texto»: cómo entra y cómo sale una sesión. */
export function formatModes(input: string, outputs: string[] | null): string {
  const salida = (outputs ?? []).map((m) => MODO[m] ?? m)
  const lista = salida.length > 1 ? `${salida.slice(0, -1).join(', ')} y ${salida.at(-1)}` : (salida[0] ?? '—')
  return `${MODO[input] ?? input} → ${lista}`
}

const shortDate = new Intl.DateTimeFormat('es-ES', { day: 'numeric', month: 'short' })
const shortDateYear = new Intl.DateTimeFormat('es-ES', { day: 'numeric', month: 'short', year: 'numeric' })
const clock = new Intl.DateTimeFormat('es-ES', { hour: '2-digit', minute: '2-digit' })

const sameDay = (a: Date, b: Date) =>
  a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate()

/** «14 nov»; con el año si no es el de `now`. Acepta ISO o una fecha sola («2026-11-14», en hora local). */
export function formatShortDate(value: string, now = new Date()): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value)
  const date = m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : parseIso(value)
  return (date.getFullYear() === now.getFullYear() ? shortDate : shortDateYear).format(date).replace('.', '')
}

/** «18:42» si es de hoy; si no, «12 nov, 18:42». */
export function formatClock(iso: string, now = new Date()): string {
  const date = parseIso(iso)
  return sameDay(date, now) ? clock.format(date) : `${formatShortDate(iso, now)}, ${clock.format(date)}`
}

/** Cuándo pasó, para una lista: «ahora mismo», «hace 12 min», «hoy, 09:05», «ayer, 22:10», «hace 3 días», «2 nov». */
export function formatRelative(iso: string, now = new Date()): string {
  const date = parseIso(iso)
  const s = (now.getTime() - date.getTime()) / 1000
  if (s < 60) return 'ahora mismo'
  if (s < 3600) return `hace ${Math.floor(s / 60)} min`
  if (sameDay(date, now)) return `hoy, ${clock.format(date)}`
  const days = Math.round((new Date(now).setHours(0, 0, 0, 0) - new Date(date).setHours(0, 0, 0, 0)) / 86_400_000)
  if (days === 1) return `ayer, ${clock.format(date)}`
  if (days < 7) return `hace ${days} días`
  return formatShortDate(iso, now)
}

/** Segundos con una cifra decimal y coma: 3,2 s. `null` → raya. */
export function formatSeconds(ms: number | null | undefined): string {
  return ms == null ? '—' : `${(ms / 1000).toLocaleString('es-ES', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} s`
}
