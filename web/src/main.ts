import '@fontsource-variable/inter'
import '@fontsource-variable/fraunces/opsz.css'
import './styles/tokens.css'
import './styles/base.css'
import './styles/motion.css'

import { createPinia } from 'pinia'
import { createApp } from 'vue'
import App from './App.vue'
import { router } from './router'

createApp(App).use(createPinia()).use(router).mount('#app')
