import { createRouter, createWebHistory } from 'vue-router'

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'dashboard', component: () => import('../views/Dashboard.vue'), meta: { title: '仪表盘' } },
    { path: '/runs', name: 'runs', component: () => import('../views/Runs.vue'), meta: { title: '决策记录' } },
    { path: '/runs/:id', name: 'run-detail', component: () => import('../views/RunDetail.vue'), meta: { title: '决策详情' } },
    { path: '/watchlist', name: 'watchlist', component: () => import('../views/Watchlist.vue'), meta: { title: '股池管理' } },
    { path: '/orders', name: 'orders', component: () => import('../views/Orders.vue'), meta: { title: '交易记录' } },
    { path: '/models', name: 'models', component: () => import('../views/Models.vue'), meta: { title: '模型管理' } },
  ],
})
