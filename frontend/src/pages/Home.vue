<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'

const d = ref(null)
const err = ref('')
const MODE_LABEL = {
  atomic: '齐套可见',
  event_first: '事件先可见',
}
async function load() {
  err.value = ''
  try { d.value = await api.dashboard() } catch (e) { err.value = e.message }
}
onMounted(load)
</script>
<template>
  <div class="panel">
    <div class="grid" v-if="d">
      <div class="stat"><div class="k">业务日</div><div class="v">{{ d.business_day }}</div></div>
      <div class="stat"><div class="k">今日事件</div><div class="v">{{ d.event_count }}</div></div>
      <div class="stat"><div class="k">核销事实</div><div class="v ticket">{{ d.fact_count }}</div></div>
      <div class="stat"><div class="k">待钉 open</div><div class="v">{{ d.open_fact_count }}</div></div>
      <div class="stat"><div class="k">待物化 pending</div><div class="v warn-ink">{{ d.pending_fact_count }}</div></div>
      <div class="stat"><div class="k">已钉 sealed</div><div class="v">{{ d.sealed_fact_count }}</div></div>
      <div class="stat"><div class="k">今日核销额</div><div class="v ticket">{{ d.redeem_total_today }}</div></div>
      <div class="stat"><div class="k">日已钉扎</div><div class="v">{{ d.day_sealed ? '是' : '否' }}</div></div>
      <div class="stat"><div class="k">冲刷回执已清空</div><div class="v">{{ d.pending_fact_count }}</div></div>
    </div>
    <p class="msg" v-if="d">
      双写可见性：<strong>{{ MODE_LABEL[d.visibility_mode] || d.visibility_mode }}</strong>
      <template v-if="d.visibility_mode === 'event_first'"> · 待物化合计 {{ d.pending_fact_total }}，pending 不计入事实与核销额</template>
    </p>
    <p class="msg" v-if="!err">票根洋红标出核销侧数字；钉扎只认 open 事实，pending 须冲刷后才能钉。</p>
    <p class="msg err" v-if="err">{{ err }}</p>
  </div>
</template>
