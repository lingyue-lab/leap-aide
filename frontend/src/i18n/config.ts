import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

// Import locale files
import enCommon from './locales/en/common.json';
import enChat from './locales/en/chat.json';
import zhCNCommon from './locales/zh-CN/common.json';
import zhCNChat from './locales/zh-CN/chat.json';

i18n.use(initReactI18next).init({
  resources: {
    en: {
      common: enCommon,
      chat: enChat,
    },
    'zh-CN': {
      common: zhCNCommon,
      chat: zhCNChat,
    },
  },
  lng: localStorage.getItem('hermes-language') || 'en',
  fallbackLng: 'en',
  defaultNS: 'common',
  ns: ['common', 'chat'],
  interpolation: {
    escapeValue: false,
  },
  react: {
    bindI18n: 'languageChanged loaded',
    bindI18nStore: 'added removed',
  },
});

i18n.on('languageChanged', (lng) => {
  localStorage.setItem('hermes-language', lng);
});

export default i18n;
