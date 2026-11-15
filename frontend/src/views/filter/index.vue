<template>
  <section class="page" data-module="filter">
    <header class="page-head">
      <div>
        <h2>数据筛选引擎管理</h2>
        <p class="page-desc">维护数据筛选的条件组合、排序规则与分页配置，为数据筛选引擎提供可验证的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记筛选模板</button>
        <button class="btn" type="button" @click="exportRows">导出数据筛选引擎清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in statCards" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
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
          <td v-for="column in columns" :key="column">
            <button v-if="column === '模板编号'" class="link" type="button" @click="openDetail(Number(row.id))">
              {{ row[column] ?? '—' }}
            </button>
            <span v-else>{{ row[column] ?? '—' }}</span>
          </td>
          <td class="row-actions">
            <button class="link" type="button" @click="openDetail(Number(row.id))">详情</button>
            <button class="link" type="button" @click="openEdit(row)">编辑</button>
            <button
              v-for="action in availableActions(String(row.status ?? ''))"
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
          <td :colspan="columns.length + 1" class="empty-state">暂无符合条件的筛选模板，可调整过滤条件或登记新模板</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条数据筛选引擎记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <!-- 登记/编辑表单：筛选条件、排序字段、分页配置、结果格式随三个必填项一起提交 -->
    <div v-if="formVisible" class="modal-mask" @click.self="closeForm">
      <div class="modal">
        <div class="modal-head">
          <h3>{{ formMode === 'create' ? '登记筛选模板' : `编辑模板 ${formState['模板编号']}` }}</h3>
          <button class="btn ghost" type="button" @click="closeForm">关闭</button>
        </div>
        <form @submit.prevent="submitForm">
          <div class="form-grid">
            <label v-for="field in formFields" :key="field" class="form-item">
              <span>{{ field }}{{ requiredFields.includes(field) ? ' *' : '' }}</span>
              <select v-if="field === '结果格式'" v-model="formState[field]">
                <option value="table">table</option>
                <option value="json">json</option>
              </select>
              <input v-else v-model="formState[field]" :placeholder="fieldHint(field)" />
            </label>
          </div>
          <p v-if="formError" class="error-text">{{ formError }}</p>
          <div class="modal-actions">
            <button class="btn ghost" type="button" @click="closeForm">取消</button>
            <button class="btn primary" type="submit">保存</button>
          </div>
        </form>
      </div>
    </div>

    <!-- 详情：直接进入该模板，配置与筛选结果集同屏展示 -->
    <div v-if="detailVisible" class="modal-mask" @click.self="closeDetail">
      <div class="modal">
        <div class="modal-head">
          <h3>筛选模板详情{{ detail ? ` · ${detail['模板编号']}` : '' }}</h3>
          <button class="btn ghost" type="button" @click="closeDetail">关闭</button>
        </div>

        <div v-if="detailError" class="error-text">{{ detailError }}</div>
        <template v-else-if="detail">
          <div class="detail-grid">
            <div v-for="field in detailFields" :key="field" class="detail-item">
              <span>{{ field }}</span>
              <strong>{{ detail[field] ?? '—' }}</strong>
            </div>
          </div>

          <div class="modal-head">
            <h3>筛选结果集（结果格式：{{ resultSet?.['结果格式'] ?? detail['结果格式'] }}）</h3>
            <span v-if="detail.hasSnapshot" class="badge">已有可用快照</span>
            <span v-else class="badge muted">尚未生成（启用模板后产出）</span>
          </div>
          <p v-if="resultMessage" :class="resultOk ? 'success-text' : 'error-text'">{{ resultMessage }}</p>

          <table v-if="resultSet && resultSet.items.length" class="result-table">
            <thead>
              <tr>
                <th v-for="column in resultColumns" :key="column">{{ column }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, index) in resultSet.items" :key="index">
                <td v-for="column in resultColumns" :key="column">{{ row[column] ?? '—' }}</td>
              </tr>
            </tbody>
          </table>
          <p v-else class="empty-state">该模板还没有可用的筛选结果集</p>
          <p v-if="resultSet" class="page-desc">
            命中 {{ resultSet.total }} 行，本页展示 {{ resultSet.items.length }} 行；条件 {{ resultSet['筛选条件'] || '—' }}，
            排序 {{ resultSet['排序字段'] || '—' }}，分页 {{ resultSet['分页配置'] || '—' }}
          </p>

          <div class="modal-actions">
            <button class="btn" type="button" :disabled="!detail.hasSnapshot" @click="rerunBatch">重跑筛选批次</button>
            <button
              v-for="action in availableActions(String(detail.status ?? ''))"
              :key="action"
              class="btn primary"
              type="button"
              @click="runAction(action, detail)"
            >
              {{ action }}
            </button>
          </div>
        </template>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null>
type ResultRow = Record<string, string | number | null>
type ResultSet = {
  模板编号?: string
  结果格式: string
  筛选条件?: string
  排序字段?: string
  分页配置?: string
  total: number
  items: ResultRow[]
  available: boolean
}
type PagePayload = { items: Row[]; total: number }
type ActionPayload = { ok: boolean; message: string; entry?: Row }

const route = useRoute()
const router = useRouter()

const ENDPOINT = '/api/filter'
const columns = ['模板编号', '模板名称', '目标数据源', '筛选条件', '排序字段', '分页配置', '结果格式', '模板状态']
const filterFields = ['模板编号', '模板名称', '筛选条件', '目标数据源']
const detailFields = columns
const requiredFields = ['模板编号', '模板名称', '目标数据源']
const formFields = ['模板编号', '模板名称', '目标数据源', '筛选条件', '排序字段', '分页配置', '结果格式']
// 动作与后端状态机保持一致：只有当前状态允许的动作才展示
const actionMatrix: Record<string, string[]> = {
  草稿: ['启用模板'],
  已启用: ['暂停模板'],
  已暂停: ['启用模板', '废弃模板'],
  已废弃: [],
}
const resultColumns = ['order_id', 'store_id', 'amount', 'status']

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const stats = ref({ total: 0, enabled: 0, paused: 0 })
const statCards = computed(() => [
  { label: '筛选模板总数', value: stats.value.total },
  { label: '已启用模板', value: stats.value.enabled },
  { label: '已暂停模板', value: stats.value.paused },
])

function availableActions(status: string): string[] {
  return actionMatrix[status] ?? []
}

async function readBody<T>(response: Response): Promise<T> {
  try {
    return (await response.json()) as T
  } catch {
    throw new Error('接口返回内容无法解析')
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  for (const field of filterFields) {
    const value = filters.value[field]?.trim()
    if (value) {
      query.set(field, value)
    }
  }
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) {
      throw new Error('筛选模板列表读取失败')
    }
    const payload = await readBody<PagePayload>(response)
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据筛选引擎列表读取失败'
  }
}

async function reloadStats() {
  try {
    const response = await request(`${ENDPOINT}/stats`)
    if (response.ok) {
      stats.value = await readBody<typeof stats.value>(response)
    }
  } catch {
    // 统计卡片失败不阻断主列表
  }
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

// ---------------------------------------------------------------- 登记/编辑表单
const formVisible = ref(false)
const formMode = ref<'create' | 'edit'>('create')
const formEditingId = ref<number | null>(null)
const formError = ref('')
const emptyForm = (): Record<string, string> => ({
  模板编号: '',
  模板名称: '',
  目标数据源: '订单表',
  筛选条件: '',
  排序字段: '',
  分页配置: '20 条/页',
  结果格式: 'table',
})
const formState = ref<Record<string, string>>(emptyForm())

function fieldHint(field: string): string {
  const hints: Record<string, string> = {
    筛选条件: '示例：store_id=88',
    排序字段: '示例：amount desc',
    分页配置: '示例：20 条/页',
  }
  return hints[field] ?? field
}

function openCreate() {
  formMode.value = 'create'
  formEditingId.value = null
  formState.value = emptyForm()
  formError.value = ''
  formVisible.value = true
}

function openEdit(row: Row) {
  formMode.value = 'edit'
  formEditingId.value = Number(row.id)
  formState.value = emptyForm()
  for (const field of formFields) {
    const value = row[field]
    if (value !== null && value !== undefined) {
      formState.value[field] = String(value)
    }
  }
  formError.value = ''
  formVisible.value = true
}

function closeForm() {
  formVisible.value = false
}

async function submitForm() {
  formError.value = ''
  const missing = requiredFields.filter((field) => !formState.value[field]?.trim())
  if (missing.length) {
    formError.value = `缺少必填字段：${missing.join('、')}`
    return
  }
  const url = formMode.value === 'create'
    ? ENDPOINT
    : `${ENDPOINT}/${formEditingId.value}`
  try {
    const response = await request(url, {
      method: formMode.value === 'create' ? 'POST' : 'PUT',
      body: JSON.stringify({ values: { ...formState.value } }),
    })
    const payload = await readBody<ActionPayload>(response)
    if (!response.ok || !payload.ok) {
      formError.value = payload.message || '筛选模板保存失败'
      return
    }
    formVisible.value = false
    await reload()
    await reloadStats()
  } catch (error) {
    formError.value = error instanceof Error ? error.message : '筛选模板保存失败'
  }
}

// ---------------------------------------------------------------- 详情与结果集
const detailVisible = ref(false)
const detail = ref<Row | null>(null)
const detailError = ref('')
const resultSet = ref<ResultSet | null>(null)
const resultMessage = ref('')
const resultOk = ref(false)

async function loadDetail(entryId: number) {
  detailError.value = ''
  resultMessage.value = ''
  try {
    const [detailResponse, resultResponse] = await Promise.all([
      request(`${ENDPOINT}/${entryId}`),
      request(`${ENDPOINT}/${entryId}/result`),
    ])
    if (!detailResponse.ok) {
      throw new Error('筛选模板详情读取失败')
    }
    detail.value = await readBody<Row>(detailResponse)
    if (resultResponse.ok) {
      resultSet.value = await readBody<ResultSet>(resultResponse)
    } else {
      resultSet.value = null
    }
    detailVisible.value = true
  } catch (error) {
    detailError.value = error instanceof Error ? error.message : '筛选模板详情读取失败'
    detailVisible.value = true
  }
}

function openDetail(entryId: number) {
  void router.push({ name: 'filter-detail', params: { id: String(entryId) } })
}

function closeDetail() {
  detailVisible.value = false
  detail.value = null
  resultSet.value = null
  if (route.name === 'filter-detail') {
    void router.push({ name: 'filter' })
  }
}

async function refreshDetailAfterAction(entryId: number) {
  await loadDetail(entryId)
  await reload()
  await reloadStats()
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await readBody<ActionPayload>(response)
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || '数据筛选引擎动作未生效，请稍后重试')
    }
    if (detailVisible.value && detail.value && Number(detail.value.id) === Number(row.id)) {
      resultMessage.value = payload.message
      resultOk.value = true
      await refreshDetailAfterAction(Number(row.id))
      resultMessage.value = payload.message
      resultOk.value = true
    } else {
      await reload()
    }
    await reloadStats()
  } catch (error) {
    const message = error instanceof Error ? error.message : '数据筛选引擎操作失败'
    if (detailVisible.value) {
      resultMessage.value = message
      resultOk.value = false
    } else {
      errorMessage.value = message
    }
  }
}

async function rerunBatch() {
  if (!detail.value) {
    return
  }
  const entryId = Number(detail.value.id)
  resultMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${entryId}/rerun`, { method: 'POST' })
    const payload = await readBody<ActionPayload>(response)
    if (!response.ok || !payload.ok) {
      // 坏批次：提示失败，页面保留上一份快照
      resultOk.value = false
      resultMessage.value = payload.message || '筛选批次失败'
    } else {
      resultOk.value = true
      resultMessage.value = payload.message
    }
    await loadDetail(entryId)
    resultOk.value = payload.ok
    resultMessage.value = payload.message
  } catch (error) {
    resultOk.value = false
    resultMessage.value = error instanceof Error ? error.message : '筛选批次执行失败'
  }
}

// 路由直达：/filter/:id 打开详情；详情关闭后回到列表
watch(
  () => route.params.id,
  (id) => {
    if (route.name === 'filter-detail' && id) {
      void loadDetail(Number(id))
    } else if (route.name === 'filter' && detailVisible.value) {
      closeDetail()
    }
  },
  { immediate: true },
)

onMounted(() => {
  void reload()
  void reloadStats()
})
</script>
