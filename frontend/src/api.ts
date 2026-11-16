import WebApp from '@twa-dev/sdk';

const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

export type Service = { id: number; name: string; duration_minutes: number };
export type Subcategory = { name: string; services: Service[] };
export type Category = { name: string; subcategories: Subcategory[] };
export type Booking = {
  id: number;
  service_id: number;
  service: string;
  category: string;
  duration_minutes: number;
  start_time: string;
  end_time: string;
  status: string;
  client_name?: string;
  telegram_id?: number;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set('Content-Type', 'application/json');
  if (WebApp.initData) headers.set('X-Telegram-Init-Data', WebApp.initData);
  const response = await fetch(`${API_URL}${path}`, { ...init, headers });
  if (!response.ok) {
    let message = 'Не вдалося виконати запит. Спробуйте ще раз.';
    try {
      const body = await response.json() as { detail?: string };
      if (body.detail) message = body.detail;
    } catch {
      // Keep the user-facing fallback for non-JSON server errors.
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export const api = {
  services: () => request<Category[]>('/api/services'),
  slots: (serviceId: number, date: string) =>
    request<{ date: string; slots: string[] }>(`/api/slots?service_id=${serviceId}&date=${date}`),
  bookings: () => request<Booking[]>('/api/bookings/my'),
  createBooking: (service_id: number, start_time: string) =>
    request<Booking>('/api/bookings', { method: 'POST', body: JSON.stringify({ service_id, start_time }) }),
  cancelBooking: (id: number) => request<{ id: number; status: string }>(`/api/bookings/${id}/cancel`, { method: 'PATCH' }),
  reschedule: (id: number, start_time: string) =>
    request<Booking>(`/api/bookings/${id}/reschedule`, { method: 'POST', body: JSON.stringify({ start_time }) }),
  notificationPreference: (enabled: boolean) =>
    request<{ enabled: boolean }>('/api/profile/notifications', { method: 'PATCH', body: JSON.stringify({ enabled }) }),
  profile: () => request<{ first_name: string; username: string | null; notifications_enabled: boolean }>('/api/profile'),
  masterBookings: () => request<Booking[]>('/api/master/bookings'),
  masterDecision: (booking_id: number, decision: 'confirm' | 'reject') =>
    request<{ id: number; status: string }>('/api/master/confirm', {
      method: 'POST',
      body: JSON.stringify({ booking_id, decision }),
    }),
};

export function telegramUser() {
  return WebApp.initDataUnsafe?.user;
}

export function telegramWebApp() {
  return WebApp;
}
