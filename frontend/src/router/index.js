import { createRouter, createWebHistory } from 'vue-router'

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'dashboard', component: () => import('../views/Dashboard.vue'), meta: { title: '战报' } },
    { path: '/strategies', name: 'strategies', component: () => import('../views/Strategies.vue'), meta: { title: '策略' } },
    { path: '/watchlist', name: 'watchlist', component: () => import('../views/Watchlist.vue'), meta: { title: '股池' } },
    { path: '/runs', name: 'runs', component: () => import('../views/Runs.vue'), meta: { title: '决策' } },
    { path: '/runs/:id', name: 'run-detail', component: () => import('../views/RunDetail.vue'), meta: { title: '决策详情' } },
    { path: '/orders', name: 'orders', component: () => import('../views/Orders.vue'), meta: { title: '成交' } },
    { path: '/models', name: 'models', component: () => import('../views/Models.vue'), meta: { title: '参赛账户' } },
    { path: '/settings', name: 'settings', component: () => import('../views/Settings.vue'), meta: { title: '设置' } },
  ],
})
