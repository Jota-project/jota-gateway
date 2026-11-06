import { defineStore } from 'pinia'
import { ref } from 'vue'

const KEY = 'jota.adminToken'

// El almacenamiento del navegador puede fallar (ventana privada, datos bloqueados):
// cada acceso va protegido y la web sigue funcionando sin recordar nada.
function read(): string | null {
  try {
    return localStorage.getItem(KEY) ?? sessionStorage.getItem(KEY)
  } catch {
    return null
  }
}

function write(token: string, remember: boolean): void {
  try {
    ;(remember ? localStorage : sessionStorage).setItem(KEY, token)
    ;(remember ? sessionStorage : localStorage).removeItem(KEY)
  } catch {
    // sin almacenamiento: el token vive solo en memoria
  }
}

function forget(): void {
  try {
    localStorage.removeItem(KEY)
    sessionStorage.removeItem(KEY)
  } catch {
    // nada que borrar
  }
}

export const useSessionStore = defineStore('session', () => {
  const token = ref<string | null>(read())

  function signIn(newToken: string, remember: boolean): void {
    token.value = newToken
    write(newToken, remember)
  }

  function signOut(): void {
    token.value = null
    forget()
  }

  return { token, signIn, signOut }
})
