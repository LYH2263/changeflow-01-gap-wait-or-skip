<script setup>
import { onMounted, ref } from 'vue'
import { getJSON, patchJSON, postJSON } from '../api'
const rows = ref([])
const err = ref('')
async function load() {
  rows.value = await getJSON('/api/streams')
}
async function toggle(row) {
  await patchJSON(`/api/streams/${row.id}`, { enabled: !row.enabled })
  await load()
}
async function reset(row) {
  if (!confirm(`重置流 #${row.id}「${row.name}」？\n将清空其游标、已应用集合、账本、隔离区、Sink 与 Runs。`)) return
  err.value = ''
  try {
    await postJSON(`/api/streams/${row.id}/reset`, {})
    await load()
  } catch (e) {
    err.value = String(e.message || e)
  }
}
onMounted(async () => {
  try { await load() } catch (e) { err.value = String(e.message || e) }
})
</script>
<template>
  <div class="page">
    <h1>变更流</h1>
    <p class="muted">每条流绑定一份 CDC JSONL 夹具与独立 apply 游标；重置后可按新缺口策略重放。</p>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <div class="panel">
      <table>
        <thead>
          <tr><th>ID</th><th>名称</th><th>实体</th><th>夹具</th><th>游标</th><th>启用</th><th>备注</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.id">
            <td>{{ r.id }}</td>
            <td>{{ r.name }}</td>
            <td>{{ r.entity_type }}</td>
            <td><code>{{ r.fixture_path }}</code></td>
            <td>{{ r.apply_cursor }}</td>
            <td><span class="tag" :class="r.enabled ? 'ok' : 'warn'">{{ r.enabled ? '是' : '否' }}</span></td>
            <td class="muted">{{ r.note }}</td>
            <td>
              <button class="ghost" @click="toggle(r)">切换启用</button>
              <button class="ghost" @click="reset(r)">重置</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
