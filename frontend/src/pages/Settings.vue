<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
const day = ref('')
const mode = ref('atomic')
const faults = ref(null)
const msg = ref('')
const err = ref('')

async function load() {
  msg.value = ''; err.value = ''
  try {
    const [d, v, f] = await Promise.all([api.getDay(), api.getVisibility(), api.getFaults()])
    day.value = d.business_day
    mode.value = v.redeem_visibility_mode
    faults.value = f
  } catch (e) { err.value = e.message }
}
async function saveDay() {
  msg.value = ''; err.value = ''
  try {
    const r = await api.setDay(day.value)
    msg.value = `业务日已设为 ${r.business_day}`
  } catch (e) { err.value = e.message }
}
async function saveMode() {
  msg.value = ''; err.value = ''
  try {
    const r = await api.setVisibility(mode.value)
    msg.value = `核销双写可见性已全局切换为：${r.redeem_visibility_mode}`
  } catch (e) { err.value = e.message }
}
async function toggleFault(key, enabled) {
  msg.value = ''; err.value = ''
  try {
    const body = { key, enabled }
    if (key === 'hold_flush' && enabled) body.hold_seconds = 8
    faults.value = await api.setFault(body)
  } catch (e) { err.value = e.message }
}
onMounted(load)
</script>
<template>
  <div class="panel">
    <div class="row">
      <label>业务日 <input v-model="day" placeholder="YYYY-MM-DD" /></label>
      <button type="button" @click="saveDay">保存</button>
    </div>
  </div>
  <div class="panel">
    <h3 class="sub-head">核销双写可见性（全局互斥，只贯彻一种）</h3>
    <div class="row">
      <label>模式
        <select v-model="mode" @change="saveMode">
          <option value="atomic">齐套可见 atomic（事件+open 事实同提交，任一失败两侧不可见）</option>
          <option value="event_first">事件先可见 event_first（事件先落库，事实 pending 待冲刷）</option>
        </select>
      </label>
    </div>
    <p class="msg">当前：<strong>{{ mode }}</strong> · 切换后新核销立即按新模式双写，存量 pending 不受影响。</p>
  </div>
  <div class="panel" v-if="faults">
    <h3 class="sub-head">故障注入（演练钩，仅测试用）</h3>
    <table>
      <thead><tr><th>故障</th><th>状态</th><th>作用路径</th><th>操作</th></tr></thead>
      <tbody>
        <tr>
          <td>fact_write_fail</td>
          <td><span class="tag" :class="faults.faults.fact_write_fail ? 'bad' : 'ok'">
            {{ faults.faults.fact_write_fail ? '布障中（一次性）' : '关闭' }}
          </span></td>
          <td>下一次事实侧写入：齐套请求失败 / 冲刷条目失败</td>
          <td>
            <button type="button" class="ghost" @click="toggleFault('fact_write_fail', !faults.faults.fact_write_fail)">
              {{ faults.faults.fact_write_fail ? '撤障' : '布障' }}
            </button>
          </td>
        </tr>
        <tr>
          <td>hold_flush</td>
          <td><span class="tag" :class="faults.faults.hold_flush ? 'bad' : 'ok'">
            {{ faults.faults.hold_flush ? `持锁 ${faults.hold_seconds}s` : '关闭' }}
          </span></td>
          <td>冲刷事务持写锁暂停，制造「冲刷中再钉」</td>
          <td>
            <button type="button" class="ghost" @click="toggleFault('hold_flush', !faults.faults.hold_flush)">
              {{ faults.faults.hold_flush ? '撤障' : '布障' }}
            </button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
  <div class="panel">
    <p class="msg" v-if="msg">{{ msg }}</p>
    <p class="msg err" v-if="err">{{ err }}</p>
  </div>
</template>
