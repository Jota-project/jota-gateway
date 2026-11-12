import type { ClientInput, ClientResponse, OutputMode } from '@/api/types'

// El formulario de un cliente: lo que se edita como texto y su paso a lo que espera el gateway, con la
// misma validación que src/models/admin_schemas.py (rangos y obligatorios).

export interface ClientForm {
  name: string
  client_type: string
  default_agent: string
  allowed_agents: string
  tts_voice: string
  output_mode: OutputMode[]
  stt_language: string
  stt_vad_thold: string
  tts_speed: string
  barge_in_enabled: boolean
  barge_in_min_chars: string
  silence_timeout_s: string
  max_silence_turns: string
  push_enabled: boolean
  tool_calls_enabled: boolean
}

const num = (n: number) => String(n).replace('.', ',')

export function toForm(c: ClientResponse): ClientForm {
  return {
    name: c.name,
    client_type: c.client_type ?? '',
    default_agent: c.default_agent ?? '',
    allowed_agents: c.allowed_agents?.join(', ') ?? '',
    tts_voice: c.tts_voice,
    output_mode: [...(c.output_mode ?? [])],
    stt_language: c.stt_language,
    stt_vad_thold: num(c.stt_vad_thold),
    tts_speed: num(c.tts_speed),
    barge_in_enabled: c.barge_in_enabled,
    barge_in_min_chars: String(c.barge_in_min_chars),
    silence_timeout_s: num(c.silence_timeout_s),
    max_silence_turns: String(c.max_silence_turns),
    push_enabled: c.push_enabled,
    tool_calls_enabled: c.tool_calls_enabled,
  }
}

const parse = (v: string) => Number(v.trim().replace(',', '.'))

/** Errores por campo (vacío si todo vale). */
export function validate(f: ClientForm): Partial<Record<keyof ClientForm, string>> {
  const e: Partial<Record<keyof ClientForm, string>> = {}
  const rango = (k: keyof ClientForm, min: number, max: number, entero: boolean, minAbierto = false) => {
    const v = parse(f[k] as string)
    const ok = Number.isFinite(v) && (minAbierto ? v > min : v >= min) && v <= max && (!entero || Number.isInteger(v))
    if (!ok) e[k] = `${entero ? 'Un número entero' : 'Un número'} ${minAbierto ? 'mayor que' : 'de'} ${num(min)}${minAbierto ? ' y hasta' : ' a'} ${num(max)}.`
  }
  if (!f.name.trim() || f.name.trim().length > 200) e.name = 'Entre 1 y 200 caracteres.'
  if (!f.tts_voice.trim()) e.tts_voice = 'Pon el nombre de una voz.'
  if (!f.stt_language.trim()) e.stt_language = 'Pon un idioma, por ejemplo «es».'
  if (!f.output_mode.length) e.output_mode = 'Elige al menos una.'
  rango('stt_vad_thold', 0, 1, false)
  rango('tts_speed', 0, 10, false, true)
  rango('barge_in_min_chars', 1, 10_000, true)
  rango('silence_timeout_s', 0, 86_400, false, true)
  rango('max_silence_turns', 1, 10_000, true)
  const permitidos = agents(f.allowed_agents)
  if (f.default_agent.trim() && permitidos && !permitidos.includes(f.default_agent.trim()))
    e.default_agent = 'Tiene que estar entre los agentes permitidos.'
  return e
}

function agents(v: string): string[] | null {
  const lista = v
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
  return lista.length ? lista : null
}

/** Lo que ha cambiado respecto al cliente, ya con los tipos del gateway (para el PATCH). */
export function diff(c: ClientResponse, f: ClientForm): ClientInput {
  const next: ClientInput = {
    name: f.name.trim(),
    client_type: (f.client_type || null) as ClientInput['client_type'],
    default_agent: f.default_agent.trim() || null,
    allowed_agents: agents(f.allowed_agents),
    tts_voice: f.tts_voice.trim(),
    output_mode: f.output_mode.length ? [...f.output_mode] : null,
    stt_language: f.stt_language.trim(),
    stt_vad_thold: parse(f.stt_vad_thold),
    tts_speed: parse(f.tts_speed),
    barge_in_enabled: f.barge_in_enabled,
    barge_in_min_chars: parse(f.barge_in_min_chars),
    silence_timeout_s: parse(f.silence_timeout_s),
    max_silence_turns: parse(f.max_silence_turns),
    push_enabled: f.push_enabled,
    tool_calls_enabled: f.tool_calls_enabled,
  }
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(next)) {
    const antes = (c as unknown as Record<string, unknown>)[k]
    if (JSON.stringify(antes ?? null) !== JSON.stringify(v ?? null)) out[k] = v
  }
  return out as ClientInput
}
