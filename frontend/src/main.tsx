import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import App from './App';
import './styles.css';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

// Безопасная инициализация Telegram WebApp
// Используем window.Telegram напрямую, чтобы избежать ошибок с обертками SDK
const initTelegram = () => {
  // @ts-ignore
  const tg = window.Telegram?.WebApp;
  
  if (tg && typeof tg.ready === 'function') {
    tg.ready();
    tg.expand();
    // Устанавливаем цвет шапки (опционально)
    if (typeof tg.setHeaderColor === 'function') {
      tg.setHeaderColor('#F8F5F2');
    }
  } else {
    console.warn('Telegram WebApp не инициализирован. Убедитесь, что вы открываете приложение внутри Telegram.');
  }
};

// Вызываем инициализацию до рендера React
initTelegram();

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </React.StrictMode>
);