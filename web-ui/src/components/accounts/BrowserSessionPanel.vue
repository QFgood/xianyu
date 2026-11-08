<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { getBrowserStatus, updateBrowserState, type BrowserStatus } from '@/api/browser'
import { useAuth } from '@/composables/useAuth'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

const emit = defineEmits<{ (e: 'mode', enabled: boolean): void }>()
const { t } = useI18n()
const { username } = useAuth()
const status = ref<BrowserStatus | null>(null)
const password = ref('')
const error = ref('')
const saving = ref(false)
let timer: ReturnType<typeof setInterval> | undefined
let mounted = true
const browserUrl = computed(() => status.value?.browser_url || `${location.protocol}//${location.hostname}:7900/vnc.html?autoconnect=1&resize=remote`)
const stateLabel = computed(() => {
  if (status.value?.busy) return t('browserSession.running')
  return t(`browserSession.states.${status.value?.state || 'needs_login'}`)
})
async function refresh() {
  try {
    const next = await getBrowserStatus()
    if (!mounted) return
    status.value = next
    emit('mode', next.enabled)
  } catch (e) {
    if (mounted) error.value = (e as Error).message
  }
}
async function change(action: 'ready' | 'pause') {
  if (!status.value?.incident_id || !password.value) return
  saving.value = true
  error.value = ''
  try {
    await updateBrowserState(action, status.value.incident_id, username.value || 'admin', password.value)
    password.value = ''
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    saving.value = false
    await refresh()
  }
}
onMounted(() => {
  void refresh()
  timer = setInterval(() => { void refresh() }, 5000)
})
onUnmounted(() => { mounted = false; clearInterval(timer) })
</script>

<template>
  <Card v-if="status?.enabled || error" class="mb-6 border-blue-200">
    <CardHeader><CardTitle>{{ t('browserSession.title') }}</CardTitle></CardHeader>
    <CardContent class="space-y-4">
      <template v-if="status?.enabled">
        <div class="flex flex-wrap items-center gap-3">
          <span class="rounded-full px-3 py-1 text-sm font-medium" :class="status.state === 'ready' ? 'bg-emerald-100 text-emerald-800' : 'bg-amber-100 text-amber-900'">{{ stateLabel }}</span>
          <span v-if="status.task_name" class="text-sm text-gray-600">{{ t('browserSession.affectedTask') }}: {{ status.task_name }}</span>
        </div>
        <p class="text-sm text-gray-600">{{ t('browserSession.description') }}</p>
        <ol class="list-inside list-decimal space-y-2 text-sm text-gray-700">
          <li>{{ t('browserSession.step1') }}</li>
          <li>{{ t('browserSession.step2') }}</li>
          <li>{{ t('browserSession.step3') }}</li>
        </ol>
        <a :href="browserUrl" target="_blank" rel="noopener noreferrer" class="inline-flex rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700">{{ t('browserSession.open') }}</a>
        <p v-if="status.notification === 'failed'" class="text-sm text-amber-800">{{ t('browserSession.notificationFailed') }}</p>
        <div class="max-w-lg space-y-2 rounded-lg bg-gray-50 p-4">
          <label for="browser-admin-password" class="block text-sm font-medium">{{ t('browserSession.password') }}</label>
          <Input id="browser-admin-password" v-model="password" type="password" autocomplete="current-password" :placeholder="t('browserSession.passwordHint')" />
          <div class="flex flex-wrap gap-2">
            <Button v-if="status.state !== 'ready'" :disabled="saving || status.busy || !password" @click="change('ready')">{{ t('browserSession.confirm') }}</Button>
            <Button v-else variant="outline" :disabled="saving || status.busy || !password" @click="change('pause')">{{ t('browserSession.pause') }}</Button>
            <Button variant="ghost" @click="refresh">{{ t('browserSession.refresh') }}</Button>
          </div>
        </div>
        <p class="text-xs text-gray-500">{{ t('browserSession.scope') }}</p>
      </template>
      <p v-if="error" role="alert" class="text-sm text-red-600">{{ error }}</p>
    </CardContent>
  </Card>
</template>
