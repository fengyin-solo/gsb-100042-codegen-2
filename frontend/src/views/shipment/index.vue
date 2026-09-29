<template>
  <section class="page" data-module="shipment">
    <header class="page-head">
      <div>
        <h2>发运单管理</h2>
        <p class="page-desc">维护发运单，围绕运单编号、发货方、收货方、发运批次做批量导入、核对、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记发运单</button>
        <button class="btn" type="button" @click="exportRows">导出发运单清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <section class="import-panel">
      <header class="import-head">
        <div>
          <h3>批量导入</h3>
          <p>先暂存 CSV 文件并逐行核对；确认后同一运单只允许一个批次落库。</p>
        </div>
        <form @submit.prevent="stageFile">
          <input ref="fileInput" accept=".csv,text/csv" type="file" @change="markFileSelected" />
          <button class="btn primary" type="submit" :disabled="!selectedFile || uploading">
            {{ uploading ? '正在暂存...' : '上传并核对' }}
          </button>
          <button
            v-if="importResult && canCommit"
            class="btn"
            type="button"
            :disabled="committing"
            @click="commitImport"
          >
            {{ committing ? '正在导入...' : `确认导入 ${importResult.valid} 行` }}
          </button>
        </form>
      </header>
      <p class="import-tip">CSV 必要列：运单编号、发货方、收货方、发运批次；可选列：货物名称、温层要求、发运日期、预计到达、运单状态。</p>
      <p v-if="importMessage" :class="['import-message', importOk ? 'success-text' : 'error-text']">{{ importMessage }}</p>

      <div v-if="importResult" class="import-result">
        <div class="import-counts">
          <span>总行数：{{ importResult.total }}</span>
          <span class="success-text">可导入：{{ importResult.valid }}</span>
          <span class="warn-text">冲突：{{ importResult.conflicts }}</span>
          <span class="error-text">无效：{{ importResult.invalid }}</span>
          <span>已落库：{{ importResult.imported }}</span>
        </div>
        <div class="table-wrap">
          <table class="data-table compact">
            <thead>
              <tr>
                <th>行号</th>
                <th>核对结果</th>
                <th>运单编号</th>
                <th>发货方</th>
                <th>收货方</th>
                <th>发运批次</th>
                <th>说明</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in importResult.rows" :key="item.row_number" :class="`row-${item.status}`">
                <td>{{ item.row_number }}</td>
                <td>{{ statusLabels[item.status] ?? item.status }}</td>
                <td>{{ item.waybill_no || '—' }}</td>
                <td>{{ item.sender || '—' }}</td>
                <td>{{ item.receiver || '—' }}</td>
                <td>{{ item.shipment_batch || '—' }}</td>
                <td>{{ item.message }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>

    <section class="summary-panel">
      <h3>发运批次汇总</h3>
      <div class="table-wrap">
        <table class="data-table compact">
          <thead>
            <tr>
              <th>发运批次</th>
              <th>运单数</th>
              <th>待发运</th>
              <th>在途</th>
              <th>已到达</th>
              <th>已签收</th>
              <th>已退回</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="batch in batchSummary" :key="String(batch['发运批次'])">
              <td>{{ batch['发运批次'] }}</td>
              <td>{{ batch['运单数'] }}</td>
              <td>{{ batch['待发运'] }}</td>
              <td>{{ batch['在途'] }}</td>
              <td>{{ batch['已到达'] }}</td>
              <td>{{ batch['已签收'] }}</td>
              <td>{{ batch['已退回'] }}</td>
            </tr>
            <tr v-if="!batchSummary.length">
              <td colspan="7" class="empty-state">暂无批次数据</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <div class="content-grid">
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in columns" :key="column">{{ column }}</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in rows"
            :key="String(row.id)"
            :class="{ selected: selectedRow?.id === row.id }"
            @click="selectRow(row)"
          >
            <td v-for="column in columns" :key="column">{{ row[column] || '—' }}</td>
            <td class="row-actions" @click.stop>
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
            <td :colspan="columns.length + 1" class="empty-state">暂无发运单数据，可先批量导入</td>
          </tr>
        </tbody>
      </table>

      <aside class="detail-panel">
        <h3>发运单明细</h3>
        <template v-if="selectedRow">
          <dl v-for="column in detailColumns" :key="column" class="detail-item">
            <dt>{{ column }}</dt>
            <dd>{{ selectedRow[column] || '—' }}</dd>
          </dl>
          <dl class="detail-item">
            <dt>内部 ID</dt>
            <dd>{{ selectedRow.id }}</dd>
          </dl>
        </template>
        <p v-else class="empty-state">点击列表中的发运单查看明细</p>
      </aside>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条发运单记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null>
type ImportRowStatus = 'valid' | 'invalid' | 'conflict' | 'imported'
type Stat = { label: string, value: number }
type BatchSummary = Record<string, string | number>

type ImportRow = {
  row_number: number
  status: ImportRowStatus
  waybill_no: string | null
  sender: string | null
  receiver: string | null
  shipment_batch: string | null
  message: string
  original: Row
  entry_id: number | null
}

type ImportResult = {
  job_id: string
  filename: string
  staged_file: string
  status: string
  message: string
  total: number
  valid: number
  conflicts: number
  invalid: number
  imported: number
  rows: ImportRow[]
  batch_summary: BatchSummary[]
  entries: Row[]
}

const ENDPOINT = '/api/shipment'
const columns = ['运单编号', '发货方', '收货方', '发运批次', '货物名称', '温层要求', '发运日期', '预计到达', '运单状态']
const detailColumns = ['status', ...columns]
const actions = ['确认发运', '确认到达', '退回货物']
const statusLabels: Record<ImportRowStatus, string> = {
  valid: '可导入',
  invalid: '无效',
  conflict: '冲突保留原记录',
  imported: '已导入',
}

const rows = ref<Row[]>([])
const total = ref(0)
const stats = ref<Stat[]>([])
const batchSummary = ref<BatchSummary[]>([])
const selectedRow = ref<Row | null>(null)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 4)
const fileInput = ref<HTMLInputElement | null>(null)
const selectedFile = ref<File | null>(null)
const uploading = ref(false)
const committing = ref(false)
const importResult = ref<ImportResult | null>(null)
const importMessage = ref('')
const importOk = ref(true)

const canCommit = computed(() => Boolean(
  importResult.value && ['staged', 'conflict_only'].includes(importResult.value.status) && importResult.value.valid > 0,
))

function resetFilters() {
  filters.value = {}
  void reload()
}

function markFileSelected() {
  selectedFile.value = fileInput.value?.files?.[0] ?? null
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '请使用批量导入暂存并确认发运单；单条审批入口暂未开放'
}

async function selectRow(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}`)
    if (!response.ok) {
      throw new Error('发运单明细读取失败')
    }
    selectedRow.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '发运单明细读取失败'
  }
}

async function stageFile() {
  if (!selectedFile.value) {
    importOk.value = false
    importMessage.value = '请先选择 CSV 文件'
    return
  }

  uploading.value = true
  errorMessage.value = ''
  importMessage.value = ''
  const form = new FormData()
  form.append('file', selectedFile.value)
  try {
    const response = await request(`${ENDPOINT}/imports`, { method: 'POST', body: form })
    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.detail ?? '文件暂存核对失败')
    }
    importResult.value = payload
    importOk.value = payload.valid > 0
    importMessage.value = payload.message
  } catch (error) {
    importOk.value = false
    importMessage.value = error instanceof Error ? error.message : '文件暂存核对失败'
  } finally {
    uploading.value = false
  }
}

async function commitImport() {
  if (!importResult.value) {
    return
  }
  committing.value = true
  errorMessage.value = ''
  importMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/imports/${importResult.value.job_id}/commit`, { method: 'POST' })
    const payload: ImportResult = await response.json()
    if (!response.ok) {
      throw new Error((payload as unknown as { detail?: string }).detail ?? '发运单导入失败')
    }
    importResult.value = payload
    importOk.value = true
    importMessage.value = payload.message
    await Promise.all([reload(), refreshSummary()])
    const firstEntry = payload.entries[0]
    if (firstEntry) {
      selectedRow.value = firstEntry
    }
  } catch (error) {
    importOk.value = false
    importMessage.value = error instanceof Error ? error.message : '发运单导入失败'
  } finally {
    committing.value = false
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message ?? payload.detail ?? '发运单动作未生效，请稍后重试')
    }
    await Promise.all([reload(), refreshSummary()])
    await selectRow(payload.entry as Row)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '发运单操作失败'
  }
}

async function refreshSummary() {
  const response = await request(`${ENDPOINT}/batch-summary`)
  if (!response.ok) {
    throw new Error('批次汇总读取失败')
  }
  const payload = await response.json()
  stats.value = payload.stats ?? []
  batchSummary.value = payload.items ?? []
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(
    Object.fromEntries(Object.entries(filters.value).filter(([, value]) => value.trim())),
  ).toString()
  try {
    const [listResponse, summaryResponse] = await Promise.all([
      request(`${ENDPOINT}?${query}`),
      request(`${ENDPOINT}/batch-summary`),
    ])
    if (!listResponse.ok) {
      throw new Error('发运单列表读取失败')
    }
    if (!summaryResponse.ok) {
      throw new Error('批次汇总读取失败')
    }
    const payload = await listResponse.json()
    const summary = await summaryResponse.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    stats.value = summary.stats ?? []
    batchSummary.value = summary.items ?? []
    if (selectedRow.value) {
      const next = rows.value.find(item => item.id === selectedRow.value?.id)
      selectedRow.value = next ? await request(`${ENDPOINT}/${next.id}`).then(res => res.json()) : null
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '发运单列表读取失败'
  }
}

onMounted(reload)
</script>
