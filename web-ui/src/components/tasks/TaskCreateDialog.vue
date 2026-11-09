<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { createTaskWithAI } from '@/api/tasks'
import type { TaskGenerateRequest } from '@/types/task.d.ts'
import { parseTaskFormDefaults } from '@/lib/taskFormQuery'
import TaskForm from '@/components/tasks/TaskForm.vue'
import { Button } from '@/components/ui/button'
import { toast } from '@/components/ui/toast'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
const { t } = useI18n()

const props = defineProps<{
  accountOptions?: { name: string; path: string }[]
}>()

const emit = defineEmits<{
  (event: 'created'): void
}>()

const route = useRoute()
const isFormOpen = ref(false)
const isSubmitting = ref(false)
const defaultAccountPath = ref('')
const defaultValues = ref({})
function resolveAccountPath(accountName: string) {
  const match = (props.accountOptions || []).find((account) => account.name === accountName)
  return match ? match.path : ''
}

async function handleCreateTask(data: TaskGenerateRequest) {
  isSubmitting.value = true
  try {
    await createTaskWithAI(data)
    emit('created')
    toast({ title: t('tasks.toasts.created') })
    isFormOpen.value = false
  } catch (error) {
    toast({
      title: t('tasks.toasts.createFailed'),
      description: (error as Error).message,
      variant: 'destructive',
    })
  } finally {
    isSubmitting.value = false
  }
}

watch(
  () => [route.query, props.accountOptions],
  () => {
    const accountName = typeof route.query.account === 'string' ? route.query.account : ''
    defaultAccountPath.value = accountName ? resolveAccountPath(accountName) : ''
    defaultValues.value = parseTaskFormDefaults(route.query)
    if (route.query.create === '1') {
      isFormOpen.value = true
    }
  },
  { immediate: true }
)

</script>

<template>
  <Dialog v-model:open="isFormOpen">
    <DialogTrigger as-child>
      <Button>{{ t('tasks.createDialog.trigger') }}</Button>
    </DialogTrigger>
    <DialogContent class="sm:max-w-[640px] max-h-[85vh] overflow-y-auto">
      <DialogHeader>
        <DialogTitle>{{ t('tasks.createDialog.title') }}</DialogTitle>
      </DialogHeader>
      <TaskForm
        mode="create"
        :account-options="accountOptions"
        :default-account="defaultAccountPath"
        :default-values="defaultValues"
        @submit="(data) => handleCreateTask(data as TaskGenerateRequest)"
      />
      <DialogFooter>
        <Button type="submit" form="task-form" :disabled="isSubmitting">
          {{ isSubmitting ? t('tasks.createDialog.submitting') : t('tasks.createDialog.submit') }}
        </Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
</template>
