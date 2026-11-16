import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AnimatePresence, motion } from 'framer-motion';
import { ArrowLeft, ArrowRight, Bell, CalendarDays, Check, ChevronRight, Clock3, Gem, Heart, House, Scissors, UserRound, X } from 'lucide-react';
import { api, Booking, Category, Service, telegramUser, telegramWebApp } from './api';

type Screen = 'onboarding' | 'home' | 'booking' | 'bookings' | 'profile' | 'admin';
type Toast = { text: string; id: number };

const categoryIcons: Record<string, string> = { Манікюр: '✳', Педикюр: '◌', Комплекс: '✧' };
const statusNames: Record<string, string> = { confirmed: 'Підтверджено', pending: 'Очікує підтвердження', cancelled: 'Скасовано', completed: 'Завершено' };
const durationLabel = (minutes: number) => minutes >= 60
  ? `${Math.floor(minutes / 60)} год${minutes % 60 ? ` ${minutes % 60} хв` : ''}`
  : `${minutes} хв`;

function localDate(date: Date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function readableDate(value: string) {
  return new Intl.DateTimeFormat('uk-UA', { weekday: 'long', day: 'numeric', month: 'long', timeZone: 'Europe/Kyiv' })
    .format(new Date(value));
}

function readableBookingDate(value: string) {
  const date = new Date(value);
  return `${new Intl.DateTimeFormat('uk-UA', { day: 'numeric', month: 'long', weekday: 'short' }).format(date)} · ${date.toLocaleTimeString('uk-UA', { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Kyiv' })}`;
}

function App() {
  const queryClient = useQueryClient();
  const webApp = useMemo(() => telegramWebApp(), []);
  const user = telegramUser();
  const [screen, setScreen] = useState<Screen>(() => localStorage.getItem('atelier-onboarded') ? 'home' : 'onboarding');
  const [onboardingSlide, setOnboardingSlide] = useState(0);
  const [step, setStep] = useState(1);
  const [category, setCategory] = useState<Category | null>(null);
  const [selectedService, setSelectedService] = useState<Service | null>(null);
  const [selectedDate, setSelectedDate] = useState(localDate(new Date()));
  const [selectedTime, setSelectedTime] = useState('');
  const [rescheduleId, setRescheduleId] = useState<number | null>(null);
  const [notificationsEnabled, setNotificationsEnabled] = useState(true);
  const [toast, setToast] = useState<Toast | null>(null);
  const mainAction = useRef<() => void>(() => undefined);
  const dates = useMemo(() => Array.from({ length: 14 }, (_, index) => {
    const date = new Date();
    date.setDate(date.getDate() + index);
    return date;
  }), []);

  const servicesQuery = useQuery({ queryKey: ['services'], queryFn: api.services, enabled: screen !== 'onboarding' });
  const bookingsQuery = useQuery({ queryKey: ['bookings'], queryFn: api.bookings, enabled: screen === 'home' || screen === 'bookings' || screen === 'profile' });
  const slotsQuery = useQuery({
    queryKey: ['slots', selectedService?.id, selectedDate],
    queryFn: () => api.slots(selectedService!.id, selectedDate),
    enabled: screen === 'booking' && step === 3 && !!selectedService,
  });
  const adminQuery = useQuery({ queryKey: ['master-bookings'], queryFn: api.masterBookings, enabled: screen === 'admin' });
  const profileQuery = useQuery({ queryKey: ['profile'], queryFn: api.profile, enabled: screen === 'profile' });
  const notify = useCallback((text: string) => {
    setToast({ text, id: Date.now() });
    window.setTimeout(() => setToast(null), 3200);
  }, []);

  const createMutation = useMutation({
    mutationFn: () => api.createBooking(selectedService!.id, `${selectedDate}T${selectedTime}:00`),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['bookings'] });
      setStep(5);
    },
    onError: (error: Error) => notify(error.message),
  });
  const rescheduleMutation = useMutation({
    mutationFn: () => api.reschedule(rescheduleId!, `${selectedDate}T${selectedTime}:00`),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['bookings'] });
      setStep(5);
    },
    onError: (error: Error) => notify(error.message),
  });
  const cancelMutation = useMutation({
    mutationFn: api.cancelBooking,
    onMutate: async (id) => {
      await queryClient.cancelQueries({ queryKey: ['bookings'] });
      const previous = queryClient.getQueryData<Booking[]>(['bookings']);
      queryClient.setQueryData<Booking[]>(['bookings'], old => old?.map(item => item.id === id ? { ...item, status: 'cancelled' } : item));
      return { previous };
    },
    onError: (error: Error, _id, context) => {
      queryClient.setQueryData(['bookings'], context?.previous);
      notify(error.message);
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['bookings'] }),
  });
  const preferenceMutation = useMutation({
    mutationFn: api.notificationPreference,
    onError: (error: Error, enabled) => {
      setNotificationsEnabled(!enabled);
      notify(error.message);
    },
  });
  const decisionMutation = useMutation({
    mutationFn: ({ id, decision }: { id: number; decision: 'confirm' | 'reject' }) => api.masterDecision(id, decision),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['master-bookings'] });
      notify('Статус запису оновлено.');
    },
    onError: (error: Error) => notify(error.message),
  });

  useEffect(() => {
    webApp?.ready?.();
    webApp?.expand?.();
    const refresh = () => {
      if (document.visibilityState === 'visible') {
        void queryClient.invalidateQueries({ queryKey: ['bookings'] });
        void queryClient.invalidateQueries({ queryKey: ['services'] });
        void queryClient.invalidateQueries({ queryKey: ['profile'] });
      }
    };
    document.addEventListener('visibilitychange', refresh);
    return () => document.removeEventListener('visibilitychange', refresh);
  }, [queryClient, webApp]);
  useEffect(() => {
    if (profileQuery.data) setNotificationsEnabled(profileQuery.data.notifications_enabled);
  }, [profileQuery.data]);

  const bookingNext = useCallback(() => {
    if (step === 1 && category) setStep(2);
    else if (step === 2 && selectedService) {
      setSelectedDate(localDate(new Date()));
      setSelectedTime('');
      setStep(3);
    } else if (step === 3 && selectedTime) setStep(4);
    else if (step === 4) {
      if (rescheduleId) rescheduleMutation.mutate();
      else createMutation.mutate();
    } else if (step === 5) setScreen('bookings');
  }, [step, category, selectedService, selectedTime, rescheduleId, rescheduleMutation, createMutation]);
  mainAction.current = bookingNext;
  const canContinue = (step === 1 && !!category) || (step === 2 && !!selectedService)
    || (step === 3 && !!selectedTime) || step === 4;
  const mainText = step === 4 ? (rescheduleId ? 'Підтвердити перенесення' : 'Підтвердити запис')
    : step === 5 ? 'Мої записи' : 'Продовжити';

  useEffect(() => {
    const mainButton = webApp?.MainButton;
    if (!mainButton || screen !== 'booking' || !canContinue) {
      mainButton?.hide();
      return;
    }
    mainButton.setParams?.({ text: mainText, is_visible: true, is_active: !createMutation.isPending && !rescheduleMutation.isPending, color: '#9b8aa6', text_color: '#ffffff' });
    const invoke = () => mainAction.current();
    mainButton.onClick?.(invoke);
    return () => {
      mainButton.offClick?.(invoke);
      mainButton.hide();
    };
  }, [canContinue, mainText, screen, webApp, createMutation.isPending, rescheduleMutation.isPending]);

  const goBack = useCallback(() => {
    if (screen === 'booking') {
      if (step > 1 && step < 5) setStep(value => value - 1);
      else { setScreen('home'); setStep(1); setRescheduleId(null); }
    } else setScreen('home');
  }, [screen, step]);

  useEffect(() => {
    const backButton = webApp?.BackButton;
    if (!backButton) return;
    if (screen !== 'home' && screen !== 'onboarding') {
      backButton.show?.();
      backButton.onClick?.(goBack);
      return () => { backButton.offClick?.(goBack); backButton.hide(); };
    }
    backButton.hide();
  }, [goBack, screen, webApp]);

  const beginBooking = () => {
    setCategory(null);
    setSelectedService(null);
    setSelectedTime('');
    setRescheduleId(null);
    setStep(1);
    setScreen('booking');
  };
  const startReschedule = (booking: Booking) => {
    const allCategories = servicesQuery.data ?? [];
    const foundCategory = allCategories.find(item => item.name === booking.category);
    const foundService = foundCategory?.subcategories.flatMap(item => item.services).find(item => item.id === booking.service_id);
    if (!foundCategory || !foundService) {
      notify('Не вдалося завантажити послугу. Спробуйте ще раз.');
      return;
    }
    setCategory(foundCategory);
    setSelectedService(foundService);
    setSelectedDate(localDate(new Date()));
    setSelectedTime('');
    setRescheduleId(booking.id);
    setStep(3);
    setScreen('booking');
  };
  const confirmCancel = (id: number) => {
    if (window.confirm('Скасувати цей запис?')) cancelMutation.mutate(id);
  };
  const completeOnboarding = () => {
    localStorage.setItem('atelier-onboarded', 'true');
    setScreen('home');
  };
  const upcoming = (bookingsQuery.data ?? []).filter(item =>
    ['confirmed', 'pending'].includes(item.status) && new Date(item.start_time) >= new Date());
  const nextBooking = [...upcoming].sort((a, b) => a.start_time.localeCompare(b.start_time))[0];

  if (screen === 'onboarding') {
    const slides = [
      { icon: <Scissors size={25} />, title: 'Час для себе', text: 'Оберіть догляд, який пасує саме вам.' },
      { icon: <CalendarDays size={25} />, title: 'Зручний час', text: 'Перегляньте вільні слоти та оберіть комфортний.' },
      { icon: <Gem size={25} />, title: 'Ідеальний візит', text: 'Ми нагадаємо про запис і чекатимемо на вас.' },
    ];
    const slide = slides[onboardingSlide];
    return <main className="onboarding">
      <div className="onboarding-brand"><span className="brand-mark">a.</span><span>ATELIER</span></div>
      <motion.section key={onboardingSlide} className="onboarding-slide" initial={false} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.2 }}>
          <div className="onboarding-art"><div className="art-ring" /><div className="art-icon">{slide.icon}</div><span className="art-sparkle">✧</span></div>
          <p className="eyebrow">ВАШ РИТУАЛ КРАСИ</p>
          <h1>{slide.title}</h1>
          <p className="muted onboarding-copy">{slide.text}</p>
      </motion.section>
      <div className="onboarding-footer">
        <div className="dots">{slides.map((_, index) => <span className={index === onboardingSlide ? 'dot active' : 'dot'} key={index} />)}</div>
        <button className="primary-button" onClick={() => onboardingSlide < 2 ? setOnboardingSlide(value => value + 1) : completeOnboarding()}>
          {onboardingSlide < 2 ? 'Далі' : 'Почати'} <ArrowRight size={17} />
        </button>
        {onboardingSlide < 2 && <button className="text-button" onClick={completeOnboarding}>Пропустити</button>}
      </div>
    </main>;
  }

  return <div className="app-shell">
    <header className="topbar">
      {screen === 'home' ? <div className="brand" onClick={() => setScreen('home')}><span className="brand-mark">a.</span><span>ATELIER</span></div>
        : <button className="icon-button" aria-label="Назад" onClick={goBack}><ArrowLeft size={20} /></button>}
      <div className="topbar-caption">{screen === 'booking' ? `ЗАПИС · КРОК ${Math.min(step, 4)} З 4` : screen === 'bookings' ? 'ВАШІ ВІЗИТИ' : screen === 'profile' ? 'ПРОФІЛЬ' : screen === 'admin' ? 'ПАНЕЛЬ МАЙСТРА' : 'МАНІКЮР · КИЇВ'}</div>
      <button className="avatar" aria-label="Профіль" onClick={() => setScreen('profile')}>{user?.first_name?.slice(0, 1) ?? '♡'}</button>
    </header>

    <motion.main key={screen + (screen === 'booking' ? step : '')} className="page-content"
      initial={false} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.22 }}>
        {screen === 'home' && <section className="home-screen">
          <div className="hero">
            <div className="hero-image"><div className="hero-monogram">a</div><span className="hero-caption">КРАСА У КОЖНІЙ ДЕТАЛІ</span></div>
            <div className="hero-content">
              <p className="eyebrow">ТИХА РОЗКІШ · KYIV</p>
              <h1>Твій час<br />для себе<span className="gold-dot">.</span></h1>
              <p className="muted">Догляд, до якого хочеться повертатися.</p>
              <button className="primary-button pulse-button" onClick={beginBooking}>Записатися <ArrowRight size={17} /></button>
            </div>
          </div>
          {bookingsQuery.isLoading ? <div className="skeleton-card" /> : nextBooking && <button className="next-visit" onClick={() => setScreen('bookings')}>
            <div className="visit-icon"><CalendarDays size={20} /></div><div className="visit-copy"><span className="eyebrow">НАЙБЛИЖЧИЙ ВІЗИТ</span><strong>{nextBooking.service}</strong><span>{readableBookingDate(nextBooking.start_time)}</span></div><ChevronRight size={18} />
          </button>}
          <div className="section-heading"><span className="eyebrow">ВАШ ДОСВІД ATELIER</span><span className="heading-rule" /></div>
          <div className="value-grid"><div><span>01</span><p>Делікатний<br />догляд</p></div><div><span>02</span><p>Ваш особистий<br />ритуал</p></div><div><span>03</span><p>Час, що належить<br />вам</p></div></div>
          <button className="quick-link" onClick={() => setScreen('bookings')}><span><CalendarDays size={18} /> Мої записи</span><ChevronRight size={18} /></button>
          <button className="quick-link" onClick={() => setScreen('profile')}><span><UserRound size={18} /> Профіль</span><ChevronRight size={18} /></button>
        </section>}

        {screen === 'booking' && step < 5 && <section className="booking-screen">
          <div className="progress-track"><span style={{ width: `${step * 25}%` }} /></div>
          {step === 1 && <div>
            <p className="eyebrow">КРОК 01 · НАПРЯМ</p><h1>Оберіть категорію</h1><p className="muted">З чого почнемо ваш ритуал?</p>
            {servicesQuery.isLoading ? <SkeletonList /> : servicesQuery.isError ? <ErrorPanel onRetry={() => void servicesQuery.refetch()} /> : <div className="category-list">
              {(servicesQuery.data ?? []).map(item => <button key={item.name} className={`category-card ${category?.name === item.name ? 'selected' : ''}`} onClick={() => { setCategory(item); setSelectedService(null); }}>
                <span className="category-symbol">{categoryIcons[item.name] ?? '✧'}</span><span><strong>{item.name}</strong><small>{item.subcategories.length} напрямки</small></span><ChevronRight size={18} />
              </button>)}
            </div>}
          </div>}
          {step === 2 && <div>
            <p className="eyebrow">КРОК 02 · ПОСЛУГА</p><h1>{category?.name}</h1><p className="muted">Оберіть те, що вам до вподоби.</p>
            {category?.subcategories.map(group => <div className="service-group" key={group.name}>
              <p className="group-label">{group.name}</p>
              {group.services.map(service => <button key={service.id} className={`service-card ${selectedService?.id === service.id ? 'selected' : ''}`} onClick={() => setSelectedService(service)}>
                <span><strong>{service.name}</strong><small><Clock3 size={13} /> {durationLabel(service.duration_minutes)}</small></span>
                <span className={`radio ${selectedService?.id === service.id ? 'checked' : ''}`}>{selectedService?.id === service.id && <Check size={12} />}</span>
              </button>)}
            </div>)}
          </div>}
          {step === 3 && <div>
            <p className="eyebrow">КРОК 03 · ДАТА І ЧАС</p><h1>Коли вам зручно?</h1><p className="muted">{selectedService?.name} · {durationLabel(selectedService?.duration_minutes ?? 0)}</p>
            <div className="date-strip">{dates.map(date => {
              const dateKey = localDate(date);
              return <button key={dateKey} className={`date-card ${selectedDate === dateKey ? 'active' : ''}`} onClick={() => { setSelectedDate(dateKey); setSelectedTime(''); }}>
                <span>{new Intl.DateTimeFormat('uk-UA', { weekday: 'short' }).format(date).replace('.', '')}</span><strong>{date.getDate()}</strong><small>{new Intl.DateTimeFormat('uk-UA', { month: 'short' }).format(date).replace('.', '')}</small>
              </button>;
            })}</div>
            <div className="slot-heading"><span>{readableDate(selectedDate)}</span><span>Europe/Kyiv</span></div>
            {slotsQuery.isLoading ? <div className="slot-grid">{Array.from({ length: 8 }, (_, i) => <div className="skeleton-slot" key={i} />)}</div>
              : slotsQuery.isError ? <ErrorPanel onRetry={() => void slotsQuery.refetch()} />
              : slotsQuery.data?.slots.length ? <div className="slot-grid">{slotsQuery.data.slots.map(slot => <button key={slot} className={`slot ${selectedTime === slot ? 'active' : ''}`} onClick={() => setSelectedTime(slot)}>{slot}</button>)}</div>
              : <div className="empty-slots"><CalendarDays size={22} /><p>На цю дату вільного часу немає.</p><span>Спробуйте обрати інший день.</span></div>}
            <p className="work-hours">Пн–Сб · 10:00–20:00 <span>·</span> Неділя — вихідний</p>
          </div>}
          {step === 4 && <div>
            <p className="eyebrow">КРОК 04 · ПЕРЕВІРТЕ ДЕТАЛІ</p><h1>Майже готово</h1><p className="muted">Переконайтеся, що все правильно.</p>
            <div className="summary-card"><span className="summary-seal"><Gem size={20} /></span><span className="eyebrow">ВАШ ВІЗИТ</span>
              <h2>{selectedService?.name}</h2><div className="summary-detail"><CalendarDays size={17} /><span>{readableDate(selectedDate)}</span></div>
              <div className="summary-detail"><Clock3 size={17} /><span>{selectedTime} · {durationLabel(selectedService?.duration_minutes ?? 0)}</span></div>
              <div className="summary-divider" /><p>Пн–Сб, з 10:00 до 20:00</p>
            </div>
            <p className="fine-print">Після підтвердження запис з’явиться у розділі «Мої записи». Ми надішлемо нагадування перед візитом.</p>
          </div>}
          {canContinue && !webApp?.initData && <button className="primary-button mobile-continue" onClick={bookingNext} disabled={createMutation.isPending || rescheduleMutation.isPending}>
            {step === 4 ? (rescheduleId ? 'Підтвердити перенесення' : 'Підтвердити запис') : 'Продовжити'} <ArrowRight size={17} />
          </button>}
        </section>}

        {screen === 'booking' && step === 5 && <section className="success-screen">
          <div className="success-art"><div className="success-ring"><Check size={43} strokeWidth={1.5} /></div><span className="confetti confetti-one">✦</span><span className="confetti confetti-two">✧</span><span className="confetti confetti-three">·</span></div>
          <p className="eyebrow">ЗАПИС ОФОРМЛЕНО</p><h1>{rescheduleId ? 'Час змінено' : 'До зустрічі!'}</h1>
          <p className="muted">Ваш час для себе вже чекає.</p>
          <div className="success-details"><strong>{selectedService?.name}</strong><span>{readableDate(selectedDate)} · {selectedTime}</span><span>{durationLabel(selectedService?.duration_minutes ?? 0)}</span></div>
          <button className="primary-button" onClick={() => { setScreen('bookings'); setStep(1); setRescheduleId(null); }}>Мої записи <ArrowRight size={17} /></button>
          <button className="text-button" onClick={() => { setScreen('home'); setStep(1); setRescheduleId(null); }}>На головну</button>
        </section>}

        {screen === 'bookings' && <section className="list-screen">
          <p className="eyebrow">ВАШ ОСОБИСТИЙ ЧАС</p><h1>Мої записи</h1>
          {bookingsQuery.isLoading ? <SkeletonList /> : bookingsQuery.isError ? <ErrorPanel onRetry={() => void bookingsQuery.refetch()} /> : (bookingsQuery.data ?? []).length === 0 ?
            <div className="empty-state"><div className="empty-icon"><CalendarDays size={25} /></div><h2>Поки що порожньо</h2><p>У вас ще немає записів. Запишіться на перший візит!</p><button className="primary-button" onClick={beginBooking}>Записатися <ArrowRight size={17} /></button></div>
            : <div className="booking-list">
            {(bookingsQuery.data ?? []).some(item => ['confirmed', 'pending'].includes(item.status) && new Date(item.start_time) >= new Date()) && <p className="group-label">Майбутні візити</p>}
            {(bookingsQuery.data ?? []).filter(item => ['confirmed', 'pending'].includes(item.status) && new Date(item.start_time) >= new Date()).map(booking => {
              const future = new Date(booking.start_time) >= new Date() && ['confirmed', 'pending'].includes(booking.status);
              return <article className="booking-item" key={booking.id}>
                <div className="booking-item-top"><span className={`status-dot ${booking.status}`} />{statusNames[booking.status] ?? booking.status}<span className="booking-date">{readableBookingDate(booking.start_time)}</span></div>
                <h2>{booking.service}</h2><p>{durationLabel(booking.duration_minutes)} <span>·</span> {booking.category}</p>
                {future && <div className="booking-actions">
                  <button className="outline-button" onClick={() => startReschedule(booking)}>Перенести</button>
                  <button className="quiet-button" onClick={() => confirmCancel(booking.id)}>Скасувати</button>
                </div>}
              </article>;
            })}
              {(bookingsQuery.data ?? []).some(item => !(['confirmed', 'pending'].includes(item.status) && new Date(item.start_time) >= new Date())) && <p className="group-label">Історія</p>}
              {(bookingsQuery.data ?? []).filter(item => !(['confirmed', 'pending'].includes(item.status) && new Date(item.start_time) >= new Date())).map(booking => <article className="booking-item" key={booking.id}><div className="booking-item-top"><span className={`status-dot ${booking.status}`} />{statusNames[booking.status] ?? booking.status}<span className="booking-date">{readableBookingDate(booking.start_time)}</span></div><h2>{booking.service}</h2><p>{durationLabel(booking.duration_minutes)} <span>·</span> {booking.category}</p></article>)}
            </div>}
        </section>}

        {screen === 'profile' && <section className="profile-screen">
          <p className="eyebrow">ЗНАЙОМСТВО БЛИЖЧЕ</p><h1>Профіль</h1>
          <div className="profile-card"><div className="profile-avatar">{user?.first_name?.slice(0, 1) ?? '♡'}</div><div><h2>{user?.first_name ?? 'Вітаємо'}</h2><span>{user?.username ? `@${user.username}` : 'Клієнтка Atelier'}</span></div></div>
          <div className="profile-stat"><span>Візитів в Atelier</span><strong>{bookingsQuery.data?.filter(item => item.status !== 'cancelled').length ?? '—'}</strong></div>
          <button className="setting-row" onClick={() => {
            const enabled = !notificationsEnabled;
            setNotificationsEnabled(enabled);
            preferenceMutation.mutate(enabled);
          }}><span><Bell size={18} /> Нагадування про візит</span><span className={`toggle ${notificationsEnabled ? 'on' : ''}`}><i /></span></button>
          <div className="profile-note"><Heart size={16} /><span>Дякуємо, що обираєте Atelier.<br />Піклуйтеся про себе з любов’ю.</span></div>
          {String(user?.id ?? '') === import.meta.env.VITE_MASTER_TELEGRAM_ID && <button className="quick-link" onClick={() => setScreen('admin')}><span>Панель майстра</span><ChevronRight size={18} /></button>}
        </section>}

        {screen === 'admin' && <section className="list-screen"><p className="eyebrow">ATELIER · КЕРУВАННЯ</p><h1>Усі записи</h1>
          {adminQuery.data && <div className="admin-stats"><div><strong>{adminQuery.data.length}</strong><span>усіх записів</span></div><div><strong>{adminQuery.data.filter(item => item.status === 'pending').length}</strong><span>очікують</span></div><div><strong>{adminQuery.data.filter(item => item.status === 'confirmed').length}</strong><span>підтверджено</span></div></div>}
          {adminQuery.isLoading ? <SkeletonList /> : adminQuery.isError ? <ErrorPanel onRetry={() => void adminQuery.refetch()} /> : <div className="booking-list">{(adminQuery.data ?? []).map(booking => <article className="booking-item" key={booking.id}><div className="booking-item-top"><span className={`status-dot ${booking.status}`} />{statusNames[booking.status]}</div><h2>{booking.service}</h2><p>{booking.client_name} · {readableBookingDate(booking.start_time)}</p>{booking.status === 'pending' && <div className="booking-actions"><button className="outline-button" disabled={decisionMutation.isPending} onClick={() => decisionMutation.mutate({ id: booking.id, decision: 'confirm' })}>Підтвердити</button><button className="quiet-button" disabled={decisionMutation.isPending} onClick={() => decisionMutation.mutate({ id: booking.id, decision: 'reject' })}>Відхилити</button></div>}</article>)}</div>}
        </section>}
    </motion.main>

    {screen !== 'booking' && <nav className="bottom-nav">
      <button className={screen === 'home' ? 'active' : ''} onClick={() => setScreen('home')}><House size={19} /><span>Головна</span></button>
      <button className={screen === 'bookings' ? 'active' : ''} onClick={() => setScreen('bookings')}><CalendarDays size={19} /><span>Записи</span></button>
      <button className={screen === 'profile' ? 'active' : ''} onClick={() => setScreen('profile')}><UserRound size={19} /><span>Профіль</span></button>
    </nav>}
    <AnimatePresence>{toast && <motion.div className="toast" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 8 }}><span>{toast.text}</span><button onClick={() => setToast(null)} aria-label="Закрити"><X size={15} /></button></motion.div>}</AnimatePresence>
  </div>;
}

function SkeletonList() {
  return <div className="skeleton-list"><div /><div /><div /></div>;
}

function ErrorPanel({ onRetry }: { onRetry: () => void }) {
  return <div className="error-panel"><p>Не вдалося завантажити. Спробуйте ще раз.</p><button className="outline-button" onClick={onRetry}>Повторити</button></div>;
}

export default App;
