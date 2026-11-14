/**
 * Copia texto. El portapapeles moderno solo existe en HTTPS o localhost; por http://IP se usa la vía
 * antigua sobre el campo que lo enseña (`campo`), que queda seleccionado. Devuelve si lo copió: si no,
 * el texto sigue seleccionado para copiarlo con el teclado.
 */
export async function copyText(text: string, campo?: HTMLInputElement | HTMLTextAreaElement | null): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(text)
    else {
      campo?.select()
      if (!document.execCommand('copy')) throw new Error('copy')
    }
    return true
  } catch {
    campo?.select()
    return false
  }
}
