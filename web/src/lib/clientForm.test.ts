import { describe, expect, it } from 'vitest'
import type { ClientResponse } from '@/api/types'
import { diff, toForm, validate } from './clientForm'

const cliente: ClientResponse = {
  id: 'c1',
  name: 'Tele del salón',
  key_fp: '3fa91c0e',
  is_active: true,
  client_type: 'screen',
  default_agent: null,
  allowed_agents: null,
  created_at: '2026-11-03T10:00:00',
  stt_language: 'es',
  stt_vad_thold: 0,
  tts_voice: 'af_heart',
  tts_speed: 1,
  barge_in_enabled: true,
  barge_in_min_chars: 5,
  output_mode: ['audio', 'text'],
  silence_timeout_s: 2,
  max_silence_turns: 3,
  push_enabled: true,
  tool_calls_enabled: false,
}

describe('clientForm', () => {
  it('sin cambios no manda nada', () => {
    expect(diff(cliente, toForm(cliente))).toEqual({})
  })
  it('manda solo lo cambiado, con comas decimales y vacíos a null', () => {
    const f = { ...toForm(cliente), name: ' Tele ', tts_speed: '1,5', output_mode: ['text' as const], default_agent: '' }
    expect(diff(cliente, f)).toEqual({ name: 'Tele', tts_speed: 1.5, output_mode: ['text'] })
  })
  it('valida como el gateway', () => {
    const f = { ...toForm(cliente), name: '', output_mode: [], tts_speed: '0', barge_in_min_chars: '2,5' }
    expect(Object.keys(validate(f)).sort()).toEqual(['barge_in_min_chars', 'name', 'output_mode', 'tts_speed'])
  })
  it('el agente por defecto tiene que estar entre los permitidos', () => {
    const f = { ...toForm(cliente), default_agent: 'casa', allowed_agents: 'main, otro' }
    expect(validate(f).default_agent).toBeDefined()
    expect(validate({ ...f, allowed_agents: 'main, casa' }).default_agent).toBeUndefined()
  })
})
