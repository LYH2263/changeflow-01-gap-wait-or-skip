<script setup>
import { computed, onMounted, ref } from 'vue'
import { getJSON, postJSON, putJSON } from '../api'

const POLICIES = [
  { value: 'stop_quarantine', label: '停隔离 stop_quarantine', desc: '缺口即停：后续事件全部进隔离区，游标停在缺口前' },
  { value: 'wait_window', label: '等待 wait_window', desc: '窗口内暂缓提交游标、缺口后事件不算已应用；窗口外裁决进隔离区' },
  { value: 'skip_gap', label: '跳过 skip_gap', desc: '跳过缺失 seq 继续应用；跳过位点记账本可审计，不写入 Sink' },
]
const streams = ref([])
const streamId = ref(1)
const last = ref(null)
const runs = ref([])
const err = ref('')
const busy = ref(false)
const policy = ref('stop_quarantine')
const waitWindow = ref(2)
const policyMsg = ref('')
const saving = ref(false)

const policyDesc = computed(() => (POLICIES.find(p => p.value === policy.value) || {}).desc || '')
const recon = computed(() => {
  if (!last.value) return ''
  const r = last.value
  const rep = r.report || {}
  const dup = rep.skipped_duplicate_count || 0
  const gap = (rep.skipped_gap_seqs || []).length
  return `对账：loaded ${r.loaded_count} = applied ${r.applied_count} + quarantined ${r.quarantined_count} + skipped(重复 ${dup}) + deferred ${r.deferred_count}`
    + (gap ? `；另有 ${gap} 个跳过位点（非加载事件，仅账本审计，不进 Sink）` : '')
})
function statusClass(s) {
  if (s === 'succeeded') return 'ok'
  if (s === 'failed') return 'danger'
  return 'warn'
}
async function refreshRuns() {
  runs.value = await getJSON('/api/runs')
}
async function loadSettings() {
  const rows = await getJSON('/api/settings')
  const map = Object.fromEntries(rows.map(r => [r.key, r.value]))
  if (map.gap_policy) policy.value = map.gap_policy
  if (map.gap_wait_window != null && map.gap_wait_window !== '') waitWindow.value = Number(map.gap_wait_window)
}
async function savePolicy() {
  saving.value = true
  policyMsg.value = ''
  err.value = ''
  try {
    await putJSON('/api/settings/gap_policy', { value: policy.value })
    await putJSON('/api/settings/gap_wait_window', { value: String(waitWindow.value) })
    policyMsg.value = '策略已保存，下一次 Apply 生效'
  } catch (e) {
    policyMsg.value = String(e.message || e)
  } finally {
    saving.value = false
  }
}
async function runApply() {
  busy.value = true
  err.value = ''
  try {
    last.value = await postJSON('/api/apply', { stream_id: streamId.value })
    streams.value = await getJSON('/api/streams')
    await refreshRuns()
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}
onMounted(async () => {
  streams.value = await getJSON('/api/streams')
  if (streams.value.length) streamId.value = streams.value[0].id
  await loadSettings()
  await refreshRuns()
})
</script>
<template>
  <div class="page">
    <h1>应用台</h1>
    <p class="muted">按游标连续序应用；缺口按策略三选一：停隔离 / 等待窗口 / 跳过前进。</p>

    <div class="panel">
      <h3>缺口策略（全局，保存后下一次 Apply 生效）</h3>
      <div class="row-actions">
        <label>策略
          <select v-model="policy">
            <option v-for="p in POLICIES" :key="p.value" :value="p.value">{{ p.label }}</option>
          </select>
        </label>
        <label v-if="policy === 'wait_window'">等待窗口（缺失 seq 数）
          <input type="number" min="0" v-model.number="waitWindow" style="width:6rem" />
        </label>
        <button class="ghost" :disabled="saving" @click="savePolicy">{{ saving ? '保存中…' : '保存策略' }}</button>
        <span v-if="policyMsg" class="muted">{{ policyMsg }}</span>
      </div>
      <p class="muted">{{ policyDesc }}</p>
    </div>

    <div class="row-actions panel">
      <label>流
        <select v-model.number="streamId">
          <option v-for="s in streams" :key="s.id" :value="s.id">{{ s.id }} · {{ s.name }}（游标 {{ s.apply_cursor }}）</option>
        </select>
      </label>
      <button :disabled="busy" @click="runApply">{{ busy ? '应用中…' : '执行 Apply' }}</button>
    </div>
    <p v-if="err" class="tag danger">{{ err }}</p>

    <div class="panel" v-if="last">
      <h3>最近一次</h3>
      <p>
        run #{{ last.id }} ·
        <span class="tag" :class="statusClass(last.status)">{{ last.status }}</span>
        · 策略 <code>{{ last.report.gap_policy }}</code><template v-if="last.report.gap_policy === 'wait_window'">（窗口 {{ last.report.wait_window }}）</template>
      </p>
      <p>
        loaded {{ last.loaded_count }} / applied {{ last.applied_count }} /
        quarantined {{ last.quarantined_count }} / skipped {{ last.skipped_count }} /
        deferred {{ last.deferred_count }}
        · cursor {{ last.cursor_before }} → {{ last.cursor_after }}
      </p>
      <p v-if="last.report.waiting" class="tag warn">等待中：缺失位点 {{ last.report.awaiting_seqs?.join(', ') }}；暂缓事件 {{ last.report.deferred_seqs?.join(', ') }}（游标未提交过缺口）</p>
      <p v-if="last.report.skipped_gap_seqs?.length" class="tag warn">跳过位点：{{ last.report.skipped_gap_seqs.join(', ') }}（仅账本审计，未写入 Sink）</p>
      <p v-if="last.report.stopped_on_gap" class="tag danger">停隔离：{{ last.report.stop_reason }}，后续事件已进隔离区</p>
      <p class="muted">{{ recon }}</p>
      <p class="muted">本流 Sink 条数 {{ last.report.sink_count_after }} · 本流隔离 {{ last.report.quarantine_count_after }}</p>
      <pre class="mono">{{ JSON.stringify(last.report, null, 2) }}</pre>
    </div>

    <div class="panel">
      <h3>历史 Runs</h3>
      <table>
        <thead>
          <tr><th>ID</th><th>流</th><th>状态</th><th>策略</th><th>loaded</th><th>applied</th><th>quarantine</th><th>skipped</th><th>deferred</th><th>cursor</th><th>时间</th></tr>
        </thead>
        <tbody>
          <tr v-for="r in runs" :key="r.id">
            <td>{{ r.id }}</td>
            <td>{{ r.stream_id }}</td>
            <td><span class="tag" :class="statusClass(r.status)">{{ r.status }}</span></td>
            <td><code>{{ r.report?.gap_policy }}</code></td>
            <td>{{ r.loaded_count }}</td>
            <td>{{ r.applied_count }}</td>
            <td>{{ r.quarantined_count }}</td>
            <td>{{ r.skipped_count }}</td>
            <td>{{ r.deferred_count }}</td>
            <td>{{ r.cursor_before }}→{{ r.cursor_after }}</td>
            <td>{{ r.created_at }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
