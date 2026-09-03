import axios from 'axios'

const client = axios.create({ baseURL: '/api', timeout: 120000 })

client.interceptors.response.use(
  (resp) => resp.data,
  (err) => {
    const detail = err.response?.data?.detail
    const message = typeof detail === 'string'
      ? detail
      : detail?.reason || (detail ? JSON.stringify(detail) : err.message)
    const apiError = new Error(message)
    apiError.status = detail?.status
    apiError.httpStatus = err.response?.status
    return Promise.reject(apiError)
  },
)

export default {
  getStatus: () => client.get('/status'),
  getPortfolio: (modelPk) => client.get('/portfolio', { params: { model_pk: modelPk } }),
  getEquityCurve: () => client.get('/equity-curve'),
  getLeaderboard: () => client.get('/leaderboard'),
  getModels: () => client.get('/models'),
  createModel: (data) => client.post('/models', data),
  updateModel: (id, data) => client.put(`/models/${id}`, data),
  deleteModel: (id) => client.delete(`/models/${id}`),
  getWatchlist: () => client.get('/watchlist'),
  addWatchlist: (code) => client.post('/watchlist', { code }),
  removeWatchlist: (code) => client.delete(`/watchlist/${code}`),
  autoSelect: () => client.post('/watchlist/auto-select'),
  triggerRun: () => client.post('/runs/trigger'),
  cancelRun: () => client.post('/runs/cancel'),
  getRuns: () => client.get('/runs'),
  getRunDetail: (id) => client.get(`/runs/${id}`),
  getDatasources: () => client.get('/datasources'),
  probeDatasources: (sourceId) =>
    client.post('/datasources/probe', null, {
      params: sourceId ? { source_id: sourceId } : {},
    }),
  testBark: () => client.post('/notifications/bark/test'),
  getLogs: (params) => client.get('/logs', { params: params || {} }),
  purgeLogs: () => client.post('/logs/purge'),
  getOrders: (modelPk) => client.get('/orders', { params: modelPk != null ? { model_pk: modelPk } : {} }),
  getMonitorEvents: () => client.get('/monitor-events'),
  getTradePlans: (params) => client.get('/trade-plans', { params: params || {} }),
  getTradePlan: (id) => client.get(`/trade-plans/${id}`),
  refreshTradePlanInformation: (id, data) =>
    client.post(`/trade-plans/${id}/refresh-information`, data),
  validateTradePlanPrice: (id, data) =>
    client.post(`/trade-plans/${id}/validate-price`, data),
  approveTradePlan: (id, data) => client.post(`/trade-plans/${id}/approve`, data),
  rejectTradePlan: (id, data) => client.post(`/trade-plans/${id}/reject`, data),
  getExecutionIntents: (params) => client.get('/execution-intents', { params: params || {} }),
  // 规则 / 因子 / 账本
  rulesStatus: () => client.get('/rules/status'),
  strategiesBoard: () => client.get('/strategies/board'),
  factorsSnapshot: () => client.get('/factors/snapshot'),
  ledgerStats: () => client.get('/ledger/stats'),
  runBacktest: (data) => client.post('/backtest/run', data || { years: 3 }),
  getFactsheet: (code) => client.get(`/factsheet/${code}`),
  // 运行时设置
  getSettings: (group) => client.get('/settings', { params: group ? { group } : {} }),
  putSettings: (values) => client.put('/settings', { values }),
  resetSettings: (body) => client.post('/settings/reset', body || {}),
  // 研究 P3
  listHypotheses: (status) => client.get('/research/hypotheses', { params: status ? { status } : {} }),
  createHypothesis: (data) => client.post('/research/hypotheses', data),
  getHypothesis: (id) => client.get(`/research/hypotheses/${id}`),
  translateHypothesis: (id) => client.post(`/research/hypotheses/${id}/translate`),
  updateHypothesisSpec: (id, spec, confirm = false) =>
    client.put(`/research/hypotheses/${id}/spec`, { spec, confirm }),
  backtestHypothesis: (id, years = 3) =>
    client.post(`/research/hypotheses/${id}/backtest`, { years }),
  promoteHypothesis: (id) => client.post(`/research/hypotheses/${id}/promote`),
  discardHypothesis: (id, reason = '') =>
    client.post(`/research/hypotheses/${id}/discard`, { reason }),
  retireHypothesis: (id, reason = '') =>
    client.post(`/research/hypotheses/${id}/retire`, { reason }),
  researchLibrary: () => client.get('/research/library'),
  researchGridRun: (data) => client.post('/research/grid/run', data || {}),
  researchGridImport: (specs, theory_prefix = '网格导入') =>
    client.post('/research/grid/import', { specs, theory_prefix }),
  researchPropose: (data) => client.post('/research/propose', data || { count: 5, mode: 'library' }),
}
