import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import WebApp from '@twa-dev/sdk'; // ВАЖНО: без фигурных скобок
import App from './App';
import './styles.css';

// Создаем клиент React Query
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

// Инициализация Telegram WebApp
if (window.Telegram?.WebApp) {
  WebApp.ready();
  WebApp.expand();
  // Устанавливаем цвет шапки под стиль Quiet Luxury
  WebApp.setHeaderColor('#F8F5F2');
  WebApp.setBackgroundColor('#F8F5F2');
} else {
  console.warn('Telegram WebApp не найден. Открыто вне Telegram?');
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </React.StrictMode>
);