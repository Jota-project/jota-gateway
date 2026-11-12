import { createRouter, createWebHistory } from 'vue-router'
import { useSessionStore } from './stores/session'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'estado', component: () => import('./views/EstadoView.vue') },
    { path: '/sesiones/:id?', name: 'sesiones', component: () => import('./views/SesionesView.vue') },
    { path: '/clientes', name: 'clientes', component: () => import('./views/ClientesView.vue') },
    { path: '/clientes/:id', name: 'cliente', component: () => import('./views/ClienteView.vue') },
    {
      path: '/conectar',
      name: 'conectar',
      component: () => import('./views/ConectarView.vue'),
      meta: { public: true },
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

// Sin token de administrador no hay nada que enseñar: todo pasa por Conectar.
router.beforeEach((to) => {
  const session = useSessionStore()
  if (!to.meta.public && !session.token) return { name: 'conectar' }
  if (to.name === 'conectar' && session.token) return { name: 'estado' }
  return true
})
