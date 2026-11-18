<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'

const preview = ref(null)
const msg = ref('')
const err = ref('')
const flushing = ref(false)

const MODE_LABEL = {
  atomic: '齐套可见 · 事件与事实同提交',
  event_first: '事件先可见 · 事实 pending 待冲刷',
}

async function load() {
  err.value = ''
  try { preview.value = await api.preview() } catch (e) { err.value = e.message }
}
async function seal() {
  msg.value = ''; err.value = ''
  try {
    const r = await api.seal({})
    msg.value = `已钉扎批次 #${r.batch_id} · ${r.fact_count} 笔 · 合计 ${r.total_amount}`
      + (r.pending_excluded ? `（另有 ${r.pending_excluded} 笔 pending 已排除）` : '')
    await load()
  } catch (e) { err.value = e.message }
}
async function flush() {
  msg.value = ''; err.value = ''
  flushing.value = true
  try {
    const r = await api.flush({})
    msg.value = `冲刷完成：物化 ${r.materialized_count} 笔`
      + (r.failed_count ? `，失败 ${r.failed_count} 笔（保持 pending 可再试）` : '')
      + `，剩余 pending ${r.remaining_pending}`
    await load()
  } catch (e) { err.value = e.message } finally { flushing.value = false }
}
onMounted(load)
</script>
<template>
  <div class="panel" v-if="preview">
    <div class="grid">
      <div class="stat"><div class="k">业务日</div><div class="v">{{ preview.business_day }}</div></div>
      <div class="stat"><div class="k">待钉 open</div><div class="v ticket">{{ preview.open_count }}</div></div>
      <div class="stat"><div class="k">待钉合计</div><div class="v ticket">{{ preview.open_total }}</div></div>
      <div class="stat"><div class="k">待物化 pending</div><div class="v warn-ink">{{ preview.pending_count }}</div></div>
      <div class="stat"><div class="k">已钉扎</div><div class="v">{{ preview.already_sealed ? '是' : '否' }}</div></div>
    </div>
    <p class="msg mode-badge">双写可见性：{{ MODE_LABEL[preview.visibility_mode] || preview.visibility_mode }}</p>
    <div class="row">
      <button type="button" @click="seal" :disabled="preview.already_sealed || preview.open_count===0">钉扎本日核销</button>
      <button type="button" class="ghost" @click="flush" :disabled="preview.pending_count===0 || flushing">
        {{ flushing ? '冲刷中…' : '冲刷 pending → open' }}
      </button>
      <button type="button" class="ghost" @click="load">刷新预览</button>
    </div>
    <p class="msg" v-if="msg">{{ msg }}</p>
    <p class="msg err" v-if="err">{{ err }}</p>
  </div>
  <div class="panel" v-if="preview && preview.pending_count">
    <h3 class="sub-head">待物化队列（预览/钉扎默认排除，不计入 sealed 明细）</h3>
    <table>
      <thead><tr><th>pending</th><th>事件</th><th>券码</th><th>活动</th><th>金额</th><th>尝试</th><th>上次错误</th></tr></thead>
      <tbody>
        <tr v-for="p in preview.pending" :key="p.id">
          <td>{{ p.id }}</td>
          <td>{{ p.event_id }}</td>
          <td>{{ p.coupon_code }}</td>
          <td>{{ p.campaign_code }}</td>
          <td>{{ p.amount }}</td>
          <td>{{ p.attempts }}</td>
          <td class="err-cell">{{ p.last_error || '—' }}</td>
        </tr>
      </tbody>
    </table>
  </div>
  <div class="panel">
    <h3 class="sub-head">open 核销事实（钉扎仅取该集合）</h3>
    <table>
      <thead><tr><th>事实ID</th><th>券码</th><th>活动</th><th>金额</th><th>状态</th></tr></thead>
      <tbody>
        <tr v-for="f in (preview?.facts || [])" :key="f.id">
          <td>{{ f.id }}</td>
          <td>{{ f.coupon_code }}</td>
          <td>{{ f.campaign_code }}</td>
          <td>{{ f.amount }}</td>
          <td><span class="tag warn">{{ f.status }}</span></td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
