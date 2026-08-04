import axios from 'axios'

const client = axios.create({ baseURL: '/api', timeout: 120000 })

client.interceptors.response.use(
  (resp) => resp.data,
  (err) => {
    const msg = err.response?.data?.detail || err.message
    return Promise.reject(new Error(typeof msg === 'string' ? msg : JSON.stringify(msg)))
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
  getRuns: () => client.get('/runs'),
  getRunDetail: (id) => client.get(`/runs/${id}`),
  getOrders: (modelPk) => client.get('/orders', { params: modelPk != null ? { model_pk: modelPk } : {} }),
  getMonitorEvents: () => client.get('/monitor-events'),
  resetAccount: () => client.post('/account/reset'),
  // 规则 / 因子 / 账本
  rulesStatus: () => client.get('/rules/status'),
  rulesRebalance: () => client.post('/rules/rebalance'),
  rulesRebalanceOne: (modelId) => client.post(`/rules/rebalance/${modelId}`),
  factorsSnapshot: () => client.get('/factors/snapshot'),
  ledgerStats: () => client.get('/ledger/stats'),
  runBacktest: (data) => client.post('/backtest/run', data || { years: 3 }),
  getFactsheet: (code) => client.get(`/factsheet/${code}`),
  // 运行时设置
  getSettings: (group) => client.get('/settings', { params: group ? { group } : {} }),
  putSettings: (values) => client.put('/settings', { values }),
  resetSettings: (body) => client.post('/settings/reset', body || {}),
}
