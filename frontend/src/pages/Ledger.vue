<script setup>
import { onMounted, ref } from 'vue'
import { getJSON } from '../api'
const rows = ref([])
const DISP_CLASS = {
  applied: 'ok',
  skipped_duplicate: 'muted',
  skipped_gap: 'warn',
  deferred_gap: 'warn',
  quarantine_gap: 'danger',
}
onMounted(async () => { rows.value = await getJSON('/api/ledger') })
</script>
<template>
  <div class="page">
    <h1>变更账本</h1>
    <p class="muted">每次 Apply 写入的事件处置记录（与报告同源）：applied / skipped_duplicate / skipped_gap / deferred_gap / quarantine_gap。</p>
    <div class="panel">
      <table>
        <thead>
          <tr><th>ID</th><th>流</th><th>run</th><th>seq</th><th>op</th><th>entity</th><th>disposition</th><th>event_ts</th></tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.id">
            <td>{{ r.id }}</td>
            <td>{{ r.stream_id }}</td>
            <td>{{ r.run_id }}</td>
            <td>{{ r.seq }}</td>
            <td>{{ r.op }}</td>
            <td>{{ r.entity_key || '—' }}</td>
            <td><span class="tag" :class="DISP_CLASS[r.disposition] || ''">{{ r.disposition }}</span></td>
            <td>{{ r.event_ts || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
