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
  getPortfolio: () => client.get('/portfolio'),
  getEquityCurve: () => client.get('/equity-curve'),
  getWatchlist: () => client.get('/watchlist'),
  addWatchlist: (code) => client.post('/watchlist', { code }),
  removeWatchlist: (code) => client.delete(`/watchlist/${code}`),
  triggerRun: () => client.post('/runs/trigger'),
  getRuns: () => client.get('/runs'),
  getRunDetail: (id) => client.get(`/runs/${id}`),
  getOrders: () => client.get('/orders'),
  resetAccount: () => client.post('/account/reset'),
}
