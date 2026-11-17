import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api, ensureSession, errorText } from '../api/client'
import type { Schemas } from '../api/types'

export const useServiceStore = defineStore('service', () => {
  const health = ref<Schemas['Health'] | null>(null)
  const error = ref('')
  const loading = ref(false)
  async function connect() {
    loading.value = true
    error.value = ''
    try {
      health.value = await api.health()
      await ensureSession()
    } catch (cause) { error.value = errorText(cause) }
    finally { loading.value = false }
  }
  return { health, error, loading, connect }
})
