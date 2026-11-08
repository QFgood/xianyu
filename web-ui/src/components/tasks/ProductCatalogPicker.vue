<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
export type ProductSelection = { catalog_id: string; memory_gb: number; storage_gb: number }
type Product = { id: string; name: string; chip: string; cpu_cores: number; gpu_cores: number; memory_options: number[]; storage_options: number[]; source_url: string; source_name: string; checked_at: string; year?: number }
const props = defineProps<{ modelValue?: ProductSelection | null }>()
const emit = defineEmits<{ (e: 'update:modelValue', value: ProductSelection | null): void; (e: 'pending', value: boolean): void; (e: 'selected', value: { selection: ProductSelection; name: string; keyword: string }): void }>()
const query = ref('Mac mini')
const products = ref<Product[]>([])
const candidate = ref<Product | null>(null)
const memory = ref('')
const storage = ref('')
const busy = ref(false)
const error = ref('')
const editing = ref(!props.modelValue)
const selected = computed(() => products.value.find(p => p.id === props.modelValue?.catalog_id))
let timer: ReturnType<typeof setTimeout> | undefined
let requestId = 0
const size = (gb: number) => gb >= 1024 ? `${gb / 1024}TB` : `${gb}GB`
async function search() {
  const id = ++requestId
  busy.value = true
  try {
    const response = await fetch(`/api/catalog/products?q=${encodeURIComponent(query.value)}`)
    if (!response.ok) throw new Error()
    const payload = await response.json()
    if (id === requestId) { products.value = payload.items; error.value = '' }
  } catch { if (id === requestId) error.value = '商品库暂时不可用，请重试。' } finally { if (id === requestId) busy.value = false }
}
function choose(product: Product) {
  candidate.value = product
  memory.value = ''; storage.value = ''
}
function confirm() {
  if (!candidate.value || !memory.value || !storage.value) return
  const selection = { catalog_id: candidate.value.id, memory_gb: Number(memory.value), storage_gb: Number(storage.value) }
  emit('update:modelValue', selection)
  emit('selected', { selection, name: `${candidate.value.name}${candidate.value.chip.includes('Pro') ? ` (${candidate.value.cpu_cores}/${candidate.value.gpu_cores}核)` : ''} ${selection.memory_gb}GB ${size(selection.storage_gb)}`, keyword: candidate.value.name })
  editing.value = false
}
watch(editing, value => emit('pending', value), { immediate: true })
watch(query, () => { candidate.value = null; clearTimeout(timer); timer = setTimeout(search, 250) })
onMounted(search)
onUnmounted(() => { clearTimeout(timer); requestId++ })
</script>

<template>
  <section class="rounded-xl border border-blue-100 bg-blue-50/50 p-4 space-y-3">
    <div class="flex items-center justify-between gap-3"><h3 class="font-semibold">从商品库选择目标</h3><button v-if="modelValue && !editing" type="button" class="text-sm text-blue-600" @click="editing = true; query = 'Mac mini'; candidate = null">更换配置</button></div>
    <template v-if="editing">
      <button v-if="modelValue" type="button" class="text-xs text-blue-600" @click="editing = false; candidate = null">取消更换，保留原配置</button>
      <p class="text-xs text-slate-500">先搜索并选择准确型号，再选择内存与内置 SSD。当前支持 Mac mini。</p>
      <input v-model="query" aria-label="搜索商品库" placeholder="搜索 Mac mini / M6 / M4 Pro…" class="w-full rounded-lg border bg-white px-3 py-2 text-sm" @keydown.enter.prevent="search" />
      <p v-if="error" role="alert" class="text-sm text-red-600">{{ error }} <button type="button" class="underline" @click="search">重试</button></p>
      <p v-if="busy" class="text-xs text-slate-500">正在搜索…</p>
      <div v-if="!candidate" class="max-h-60 space-y-2 overflow-y-auto">
        <button v-for="product in products" :key="product.id" type="button" class="flex w-full items-center justify-between gap-2 rounded-lg border bg-white p-3 text-left hover:border-blue-400" @click="choose(product)">
          <span><span class="block text-sm font-medium">{{ product.name }}{{ product.year ? ` (${product.year})` : '' }}</span><span class="block text-xs text-slate-500">{{ product.cpu_cores }} 核 CPU · {{ product.gpu_cores }} 核 GPU · Apple 官方规格</span></span><span class="text-xs text-blue-600 whitespace-nowrap">选择型号 →</span>
        </button>
        <p v-if="!products.length && !busy && !error" class="text-sm text-slate-500 py-3">商品库没有匹配型号。请修改搜索词；不会自动用其他代际代替。</p>
      </div>
      <div v-if="candidate" class="rounded-lg border bg-white p-3 space-y-3">
        <div class="flex justify-between gap-3"><p class="text-sm font-semibold">{{ candidate.name }} · {{ candidate.cpu_cores }}/{{ candidate.gpu_cores }} 核</p><button type="button" class="text-xs text-blue-600" @click="candidate = null">重新选择型号</button></div>
        <div class="grid grid-cols-2 gap-3">
          <label class="text-xs text-slate-500">统一内存<select v-model="memory" aria-label="选择目标内存" class="mt-1 w-full border rounded-md p-2 bg-white text-sm text-slate-900"><option value="" disabled>请选择内存</option><option v-for="gb in candidate.memory_options" :key="gb" :value="String(gb)">{{ size(gb) }}</option></select></label>
          <label class="text-xs text-slate-500">内置 SSD<select v-model="storage" aria-label="选择目标内置SSD" class="mt-1 w-full border rounded-md p-2 bg-white text-sm text-slate-900"><option value="" disabled>请选择SSD</option><option v-for="gb in candidate.storage_options" :key="gb" :value="String(gb)">{{ size(gb) }}</option></select></label>
        </div>
        <a :href="candidate.source_url" target="_blank" rel="noopener noreferrer" class="block text-xs text-blue-600">查看 {{ candidate.source_name }} · 核对日期 {{ candidate.checked_at }} ↗</a>
        <button type="button" class="rounded-lg bg-blue-600 px-4 py-2 text-sm text-white disabled:opacity-40" :disabled="!memory || !storage" @click="confirm">确认此型号与配置</button>
      </div>
    </template>
    <div v-else-if="modelValue" class="space-y-2">
      <p class="text-sm font-medium">{{ selected?.name || modelValue.catalog_id }}<span v-if="selected"> · {{ selected.cpu_cores }}/{{ selected.gpu_cores }} 核</span> · {{ modelValue.memory_gb }}GB · {{ size(modelValue.storage_gb) }} 内置 SSD</p>
      <p class="text-xs text-slate-500">已锁定所选型号、芯片、核心数和容量。下面的补充说明不能覆盖这些配置。</p>
    </div>
  </section>
</template>
