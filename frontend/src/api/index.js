import axios from 'axios'

const client = axios.create({ baseURL: '/api', timeout: 30000 })

client.interceptors.response.use(
  (resp) => resp.data,
  (err) => {
    const msg = err.response?.data?.detail || err.message
    return Promise.reject(new Error(msg))
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
  triggerRun: () => client.post('/runs/trigger'),
  getRuns: () => client.get('/runs'),
  getRunDetail: (id) => client.get(`/runs/${id}`),
  getOrders: () => client.get('/orders'),
  getMonitorEvents: () => client.get('/monitor-events'),
  resetAccount: () => client.post('/account/reset'),
}
