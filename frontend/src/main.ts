import { createApp } from 'vue';
import { createRouter, createWebHistory } from 'vue-router';
import App from './App.vue';
import './styles.css';
import { watch } from 'vue';
import { locale } from './i18n';
import PricingView from './views/PricingView.vue';
import AdminView from './views/AdminView.vue';

const router = createRouter({ history: createWebHistory(), routes: [
  { path: '/', component: PricingView },
  { path: '/admin', component: AdminView },
] });
watch(locale, value => { document.documentElement.lang = value === 'zh' ? 'zh-CN' : 'en'; }, { immediate: true });
createApp(App).use(router).mount('#app');
