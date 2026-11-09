<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'

type Summary = { count: number; min: number | null; max: number | null; median: number | null; p25: number | null; p75: number | null }
type Sample = { item_id: string; title: string; price: number | null; link: string; seller: Record<string, unknown>; classification: { model: string; condition: string; reason: string; error?: string; match_status?: string; identity_evidence?: string } }
type Report = { run_id: string; task_key: string; cohort: string; task_name: string; created_at: string; status: string; target: string; sample_count: number; title_rejected?: number; summary: Summary; groups: Record<string, Summary>; samples: Sample[]; excluded: Sample[]; errors: string[]; scope: Record<string, unknown> }
type Batch = { created_at: string; id: string; task_key: string; task_name: string; state: string; collection_status: string; collected: number; classified: number; failed: number; target: Record<string, unknown> }
const batches = ref<Batch[]>([])
const reanalyzing = ref(false)
const selectedBatch = ref('')
const reports = ref<Report[]>([])
const task = ref('')
const selectedRun = ref('')
const loading = ref(false)
const error = ref('')
const taskOptions = computed(() => [...new Map([...batches.value, ...reports.value].map(r => [r.task_key, r.task_name])).entries()])
const history = computed(() => reports.value.filter(r => r.task_key === task.value))
const current = computed(() => history.value.find(r => r.run_id === selectedRun.value) || history.value[0])
const currentBatch = computed(() => {
  const explicit = selectedBatch.value || selectedRun.value
  if (explicit) return batches.value.find(b => b.id === explicit)
  const latest = batches.value.find(b => b.task_key === task.value)
  if (latest && (!current.value || new Date(latest.created_at).getTime() > new Date(current.value.created_at).getTime())) return latest
  return batches.value.find(b => b.id === current.value?.run_id)
})
const working = computed(() => ['collecting', 'classifying'].includes(currentBatch.value?.state || ''))
const phaseLabels: Record<string, string> = { collecting: '① 采集商品详情', collected: '① 采集已结束 · 等待分析', classifying: '② AI正在理解详情并核对目标', completed: '② AI分析完成', classification_failed: '② AI分析有失败项，可重新分析' }
const categoryLabels: Record<string, string> = { not_target: '不符合目标', insufficient: '信息不足，待确认', condition_or_price: '成色、功能或报价不符合', failed: 'AI分类调用失败' }
const displayLimits = ref<Record<string, number>>({})
const categories = computed(() => {
  const result: Record<string, Sample[]> = { not_target: [], insufficient: [], condition_or_price: [], failed: [] }
  for (const sample of current.value?.excluded || []) {
    const analysis = sample.classification
    const key = analysis.error ? 'failed' : analysis.match_status === 'no_match' ? 'not_target' : analysis.match_status === 'unknown' ? 'insufficient' : 'condition_or_price'
    result[key]!.push(sample)
  }
  return result
})
async function reanalyze() {
  const id = currentBatch.value?.id || current.value?.run_id
  if (!id || reanalyzing.value || working.value) return
  reanalyzing.value = true
  try {
    const response = await fetch(`/api/market/reports/${encodeURIComponent(id)}/reanalyze`, { method: 'POST' })
    const data = await response.json()
    if (!response.ok) throw new Error(data.detail || '无法提交重新分析')
    selectedBatch.value = data.batch_id
    selectedRun.value = data.batch_id
    await refresh()
  } catch (e) { error.value = e instanceof Error ? e.message : '无法提交重新分析' }
  finally { reanalyzing.value = false }
}
const labels: Record<string, string> = { new: '全新', like_new: '几乎全新', light_wear: '轻微痕迹' }
const money = (value: number | null | undefined) => value == null ? '—' : `¥${value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`
const date = (value: string) => new Date(value).toLocaleString('zh-CN')
function safeLink(link: string) {
  try { const url = new URL(link); return ['www.goofish.com', 'goofish.com'].includes(url.hostname) && url.protocol === 'https:' ? url.href : '#' } catch { return '#' }
}
async function refresh() {
  loading.value = true
  try {
    const response = await fetch('/api/market/reports?limit=200')
    if (!response.ok) throw new Error('行情加载失败')
    reports.value = await response.json()
    const batchResponse = await fetch('/api/market/batches?limit=200')
    if (batchResponse.ok) batches.value = await batchResponse.json()
    if (!task.value || !taskOptions.value.some(([key]) => key === task.value)) task.value = taskOptions.value[0]?.[0] || ''
    error.value = ''
  } catch { error.value = '暂时无法加载行情，请稍后刷新。' } finally { loading.value = false }
}
let timer: ReturnType<typeof setInterval>
onMounted(() => { refresh(); timer = setInterval(refresh, 5000) })
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <div class="max-w-7xl mx-auto space-y-6 p-4 sm:p-8">
    <header class="flex flex-wrap items-start justify-between gap-4">
      <div><h1 class="text-2xl font-bold text-slate-900">市场行情</h1><p class="text-sm text-slate-500 mt-2">目标配置的在售标价快照 · 有效样本或价格变化时推送</p></div>
      <div class="flex gap-2"><button class="rounded-lg bg-blue-600 px-4 py-2 text-sm text-white disabled:opacity-50" :disabled="(!current && !currentBatch) || working || reanalyzing" title="重新分析已保存的详情和目标，不访问闲鱼页面；旧报告使用当前任务目标复核本地保存详情" @click="reanalyze">{{ reanalyzing ? '提交中…' : currentBatch?.state === 'classifying' ? '分析中…' : '重新分析' }}</button>
      <button class="rounded-lg border bg-white px-4 py-2 text-sm" :disabled="loading" @click="refresh">{{ loading ? '加载中…' : '刷新行情' }}</button></div>
    </header>
    <p v-if="error" role="alert" class="text-red-600">{{ error }}</p>
    <section v-if="currentBatch" class="rounded-xl border border-blue-100 bg-blue-50 p-4 text-sm space-y-2" aria-live="polite">
      <p class="font-semibold">{{ phaseLabels[currentBatch.state] || currentBatch.state }} · {{ currentBatch.task_name }}</p>
      <p>已保存详情 {{ currentBatch.collected }} 件 · 已完成分类 {{ currentBatch.classified }} 件 · 调用失败 {{ currentBatch.failed }} 件</p>
      <p class="text-xs text-slate-500">采集和分析分阶段执行；AI使用已保存的完整详情与本批次目标。重新分析不重新打开闲鱼。{{ working && current ? '下方暂时保留上次报告，完成后更新。' : '' }}</p>
    </section>
    <div v-if="!reports.length && !currentBatch && !loading" class="rounded-xl border bg-white p-8 space-y-3">
      <h2 class="font-semibold">等待第一轮行情采样</h2><p class="text-slate-500">在任务管理中从商品库选择准确型号、内存和内置硬盘，运行任务后将在这里展示。历史购买推荐不会作为行情样本。</p>
      <RouterLink to="/tasks" class="inline-block text-blue-600">前往任务管理 →</RouterLink>
    </div>
    <template v-if="current">
      <div class="flex flex-wrap gap-3">
        <select v-model="task" aria-label="选择行情任务" class="border rounded-lg p-2 bg-white" @change="selectedRun = ''; selectedBatch = ''"><option v-for="[key, name] in taskOptions" :key="key" :value="key">{{ name }}</option></select>
        <select v-model="selectedRun" @change="selectedBatch = ''; displayLimits = {}" aria-label="选择采样批次" class="border rounded-lg p-2 bg-white"><option value="">最新采样</option><option v-for="r in history" :key="r.run_id" :value="r.run_id">{{ date(r.created_at) }} {{ r.status === 'complete' ? '完整' : '不完整' }}</option></select>
      </div>
      <section class="rounded-xl border bg-white p-5 space-y-2">
        <div class="flex flex-wrap justify-between gap-2"><h2 class="font-semibold text-lg">{{ current.task_name }}</h2><span :class="current.status === 'complete' ? 'text-emerald-700' : 'text-amber-700'">{{ current.status === 'complete' ? '本轮采样完成' : '本轮采样不完整 · 不推送行情变化' }}</span></div>
        <p class="text-sm text-slate-600 whitespace-pre-wrap">{{ current.target }}</p>
        <p class="text-xs text-slate-500">{{ date(current.created_at) }} · 搜索词 {{ current.scope.keyword }} · {{ current.scope.max_pages || 1 }} 页 · {{ current.scope.personal_only ? '仅个人卖家' : '全部卖家' }} · {{ current.scope.free_shipping ? '仅包邮' : '不限运费' }} · 地区 {{ current.scope.region || '全国' }} · 价格 {{ current.scope.min_price || '不限' }}–{{ current.scope.max_price || '不限' }}</p>
        <p class="text-sm text-slate-600">配置、报价、成色及功能符合：{{ current.sample_count }} 件；不匹配、信息不明或成色/功能不符：{{ current.excluded.length }} 件。标题已排除明确非目标 {{ current.title_rejected || 0 }} 件。信用信息来自商品详情页。</p>
        <p v-if="current.errors.length" class="text-sm text-amber-700">{{ current.errors.join('；') }}</p>
      </section>
      <div class="grid gap-4 sm:grid-cols-3">
        <section v-for="(group, condition) in current.groups" :key="condition" class="rounded-xl border bg-white p-5 space-y-2">
          <h3 class="text-sm text-slate-500">{{ labels[condition] }} · {{ group.count }} 件</h3>
          <p class="text-2xl font-semibold">{{ money(group.median) }}<span class="ml-2 text-xs font-normal text-slate-400">中位数</span></p>
          <p class="text-xs text-slate-500">范围 {{ money(group.min) }}–{{ money(group.max) }}</p>
          <p class="text-xs text-slate-500">{{ group.count >= 4 ? `中间50% ${money(group.p25)}–${money(group.p75)}` : '样本较少，不推断市场趋势' }}</p>
        </section>
      </div>
      <p class="text-sm text-slate-500">这是本轮搜索范围内的卖家标价，不是成交价或全市场报价。未再观察到的商品不代表已售。仅统计全新、几乎全新、轻微痕迹且功能正常的商品；故障机、明显磨损及信息不明不计入有效样本。</p>
      <section class="rounded-xl border bg-white overflow-hidden">
        <h2 class="font-semibold p-5 border-b">有效样本</h2>
        <div class="overflow-x-auto"><table class="w-full text-sm text-left"><thead class="bg-slate-50 text-slate-500"><tr><th class="p-4">商品 / 配置</th><th class="p-4">标价</th><th class="p-4">成色</th><th class="p-4">详情页信用</th></tr></thead><tbody>
          <tr v-for="sample in current.samples" :key="sample.item_id" class="border-t"><td class="p-4 min-w-64"><a :href="safeLink(sample.link)" target="_blank" rel="noopener noreferrer" class="text-blue-600">{{ sample.title }}</a><p class="text-xs text-slate-500 mt-1">{{ sample.classification.model }}</p></td><td class="p-4 font-semibold whitespace-nowrap">{{ money(sample.price) }}</td><td class="p-4 whitespace-nowrap">{{ labels[sample.classification.condition] }}</td><td class="p-4">{{ sample.seller['卖家信用等级'] || sample.seller['卖家芝麻信用'] || '详情页未提供' }}</td></tr>
          <tr v-if="!current.samples.length"><td colspan="4" class="p-6 text-slate-500">本轮没有可确认的整机配置和报价。</td></tr>
        </tbody></table></div>
      </section>
      <details v-for="(items, category) in categories" :key="category" class="rounded-xl border bg-white p-5">
        <summary class="cursor-pointer font-medium">{{ categoryLabels[category] }}（{{ items.length }}）</summary>
        <p v-if="category === 'failed'" class="text-xs text-amber-700 mt-3">这是模型服务或响应格式问题，不代表商品不符合目标。可用页面顶部“重新分析”重试已保存详情。</p>
        <div v-for="sample in items.slice(0, displayLimits[category] || 20)" :key="sample.item_id" class="border-t mt-3 pt-3 text-sm space-y-1">
          <a :href="safeLink(sample.link)" target="_blank" rel="noopener noreferrer" class="text-blue-600 line-clamp-2 break-words">{{ sample.title }}</a>
          <p class="text-slate-500 line-clamp-2 break-words">{{ sample.classification.reason }}</p>
          <details class="text-xs text-slate-500"><summary class="cursor-pointer">查看分析依据</summary><p class="whitespace-pre-wrap break-words mt-2">{{ sample.classification.identity_evidence || sample.classification.reason }}</p><p v-if="sample.classification.error">错误类别：{{ sample.classification.error }}</p></details>
        </div>
        <button v-if="items.length > (displayLimits[category] || 20)" class="text-blue-600 mt-4 text-sm" @click="displayLimits[category] = (displayLimits[category] || 20) + 20">再显示20条</button>
      </details>
      <section class="rounded-xl border bg-white p-5"><h2 class="font-semibold mb-3">采样历史</h2><div v-for="r in history.slice(0, 12)" :key="r.run_id" class="flex flex-wrap justify-between gap-2 border-t py-3 text-sm"><button class="text-blue-600" @click="selectedRun = r.run_id">{{ date(r.created_at) }}</button><span>{{ r.sample_count }} 个配置匹配样本 · {{ r.status === 'complete' ? '完整' : '不完整' }}{{ r.cohort !== current.cohort ? ' · 目标或采样范围不同' : '' }}</span></div></section>
    </template>
  </div>
</template>
