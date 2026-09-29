<template>
  <section class="page" data-module="shipment">
    <header class="page-head">
      <div>
        <h2>发运单管理</h2>
        <p class="page-desc">批量导入发运单，先暂存文件逐行核对运单编号、发货方、收货方、发运批次，核对通过后再落库驱动批次汇总与列表重算。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="triggerImport">批量导入</button>
        <button class="btn" type="button" @click="exportRows">导出发运单清单</button>
        <input ref="fileInput" type="file" accept=".csv" style="display:none" @change="onFileChange" />
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <!-- 批次汇总：导入后与发运单列表、明细同时重算 -->
    <div class="panel">
      <div class="panel-head">
        <h3>批次汇总</h3>
        <button class="btn ghost" type="button" @click="loadSummary">重算汇总</button>
      </div>
      <table class="data-table">
        <thead>
          <tr>
            <th>发运批次</th>
            <th>运单总数</th>
            <th>待发运</th>
            <th>在途</th>
            <th>已到达</th>
            <th>已签收</th>
            <th>已退回</th>
            <th>异常数</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in summary" :key="row.发运批次">
            <td>{{ row.发运批次 }}</td>
            <td>{{ row.运单总数 }}</td>
            <td>{{ row.待发运 }}</td>
            <td>{{ row.在途 }}</td>
            <td>{{ row.已到达 }}</td>
            <td>{{ row.已签收 }}</td>
            <td>{{ row.已退回 }}</td>
            <td>{{ row.异常数 }}</td>
          </tr>
          <tr v-if="!summary.length">
            <td colspan="8" class="empty-state">暂无批次汇总数据</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 导入暂存预览：逐行核对结果 -->
    <div v-if="staged" class="panel import-panel">
      <div class="panel-head">
        <h3>导入预览 · {{ staged.批次编号 }}</h3>
        <span class="muted">{{ staged.文件名称 }} · 状态：{{ staged.状态 }}</span>
      </div>
      <div class="import-counts">
        <span>总行数 {{ staged.总行数 }}</span>
        <span class="ok-text">有效 {{ staged.有效行数 }}</span>
        <span class="error-text">无效 {{ staged.无效行数 }}</span>
        <template v-if="staged.状态 === '已落库'">
          <span class="ok-text">新增 {{ staged.新增行数 }}</span>
          <span class="warn-text">冲突 {{ staged.冲突行数 }}</span>
          <span class="muted">重复 {{ staged.重复行数 }}</span>
        </template>
      </div>
      <table class="data-table">
        <thead>
          <tr>
            <th>行号</th>
            <th>运单编号</th>
            <th>发货方</th>
            <th>收货方</th>
            <th>发运批次</th>
            <th>核对结果</th>
            <th>原因</th>
            <th>落库结果</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in staged.rows" :key="row.行号">
            <td>{{ row.行号 }}</td>
            <td>{{ row.运单编号 || '—' }}</td>
            <td>{{ row.发货方 || '—' }}</td>
            <td>{{ row.收货方 || '—' }}</td>
            <td>{{ row.发运批次 || '—' }}</td>
            <td>
              <span v-if="row.核对结果 === '有效'" class="ok-text">有效</span>
              <span v-else class="error-text">无效</span>
            </td>
            <td>{{ row.原因 || '—' }}</td>
            <td>
              <span v-if="!row.落库结果">—</span>
              <span v-else-if="row.落库结果 === '新增'" class="ok-text">{{ row.落库结果 }}</span>
              <span v-else-if="row.落库结果 === '冲突'" class="warn-text">{{ row.落库结果 }}（保留原记录）</span>
              <span v-else class="muted">{{ row.落库结果 }}</span>
            </td>
          </tr>
        </tbody>
      </table>
      <div class="import-actions">
        <button
          v-if="staged.状态 !== '已落库'"
          class="btn primary"
          type="button"
          :disabled="committing"
          @click="commitImport"
        >{{ committing ? '落库中…' : '确认导入并落库' }}</button>
        <button class="btn ghost" type="button" @click="cancelImport">关闭预览</button>
      </div>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无发运单数据，可先批量导入或登记发运单</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条发运单记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request, uploadFile } from '@/api/client'

type Row = Record<string, string | number | null>

interface ImportRow {
  行号: number
  运单编号: string
  发货方: string
  收货方: string
  发运批次: string
  核对结果: string
  原因: string | null
  落库结果: string | null
}

interface StagedBatch {
  id: number
  批次编号: string
  文件名称: string
  状态: string
  总行数: number
  有效行数: number
  无效行数: number
  新增行数: number
  冲突行数: number
  重复行数: number
  rows: ImportRow[]
}

interface SummaryRow {
  发运批次: string
  运单总数: number
  待发运: number
  在途: number
  已到达: number
  已签收: number
  已退回: number
  异常数: number
}

const ENDPOINT = '/api/shipment'
const columns = ["运单编号", "发货方", "收货方", "发运批次", "货物名称", "温层要求", "发运日期", "预计到达", "运单状态"]
const actions = ["确认发运", "确认到达", "退回货物"]
const statuses = ["待发运", "在途", "已到达", "已签收", "已退回"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = ['运单编号', '发货方', '收货方']

const summary = ref<SummaryRow[]>([])
const staged = ref<StagedBatch | null>(null)
const committing = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

const stats = computed(() => [
  { label: '待发运单', value: rows.value.filter((r) => r.status === '待发运').length },
  { label: '在途运单', value: rows.value.filter((r) => r.status === '在途').length },
  { label: '今日签收', value: rows.value.filter((r) => r.status === '已签收').length },
])

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function triggerImport() {
  errorMessage.value = ''
  fileInput.value?.click()
}

async function onFileChange(event: Event) {
  const target = event.target as HTMLInputElement
  const file = target.files?.[0]
  if (!file) return
  errorMessage.value = ''
  try {
    const payload = await uploadFile<{ ok: boolean; message: string; batch: StagedBatch }>(
      `${ENDPOINT}/import/stage`,
      file,
    )
    staged.value = payload.batch
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '文件暂存失败'
  } finally {
    target.value = ''
  }
}

async function commitImport() {
  if (!staged.value) return
  committing.value = true
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/import/${staged.value.id}/commit`, { method: 'POST' })
    if (!response.ok) {
      throw new Error('导入落库未生效，请稍后重试')
    }
    const payload = await response.json()
    staged.value = payload.batch
    // 导入结果驱动批次汇总、发运单列表与明细同时重算
    await Promise.all([reload(), loadSummary()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导入落库失败'
  } finally {
    committing.value = false
  }
}

function cancelImport() {
  staged.value = null
}

async function loadSummary() {
  try {
    const response = await request(`${ENDPOINT}/batch-summary`)
    if (!response.ok) {
      throw new Error('批次汇总读取失败')
    }
    const payload = await response.json()
    summary.value = payload.items ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '批次汇总读取失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('发运单动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '发运单操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('发运单列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '发运单列表读取失败'
  }
}

onMounted(() => {
  void reload()
  void loadSummary()
})
</script>

<style scoped>
.panel { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px; margin-bottom: 12px; }
.panel-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.panel-head h3 { margin: 0; font-size: 15px; }
.muted { color: var(--muted); font-size: 12px; }
.ok-text { color: #1a7f37; }
.warn-text { color: #9a6700; }
.error-text { color: #b42318; }
.import-panel { border-color: var(--brand); }
.import-counts { display: flex; gap: 16px; font-size: 13px; margin-bottom: 8px; }
.import-actions { display: flex; gap: 8px; margin-top: 8px; }
</style>
