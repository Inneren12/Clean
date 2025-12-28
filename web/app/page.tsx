'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  QuickChips,
  type ChipOption,
  SummaryCard,
  type SummaryFieldData,
  StepProgress,
  PrimaryCTA,
  AlwaysVisibleHandoff,
} from '@/components';

type ChatMessage = {
  role: 'user' | 'bot';
  text: string;
};

type EstimateBreakdown = {
  base_hours: number;
  multiplier: number;
  extra_hours: number;
  total_cleaner_hours: number;
  min_cleaner_hours_applied: number;
  team_size: number;
  time_on_site_hours: number;
  billed_cleaner_hours: number;
  labor_cost: number;
  add_ons_cost: number;
  discount_amount: number;
  total_before_tax: number;
};

type EstimateResponse = {
  pricing_config_id: string;
  pricing_config_version: string;
  config_hash: string;
  rate: number;
  team_size: number;
  time_on_site_hours: number;
  billed_cleaner_hours: number;
  labor_cost: number;
  discount_amount: number;
  add_ons_cost: number;
  total_before_tax: number;
  assumptions: string[];
  missing_info: string[];
  confidence: number;
  breakdown?: EstimateBreakdown | null;
};

// UI Contract Extension Types (S2-A)
type Choice = {
  id: string;
  label: string;
  value?: string | null;
};

type ChoicesConfig = {
  items: Choice[];
  multi_select?: boolean;
  selection_type?: 'button' | 'chip';
};

type StepInfo = {
  current_step: number;
  total_steps: number;
  step_label?: string | null;
  remaining_questions?: number | null;
};

type SummaryField = {
  key: string;
  label: string;
  value: string | number | boolean | null;
  editable?: boolean;
  field_type?: 'text' | 'number' | 'select' | 'boolean';
  options?: Choice[] | null;
};

type SummaryPatch = {
  title?: string | null;
  fields: SummaryField[];
};

type UIHint = {
  show_summary?: boolean | null;
  show_confirm?: boolean | null;
  show_choices?: boolean | null;
  show_progress?: boolean | null;
  minimize_text?: boolean | null;
};

type ChatTurnResponse = {
  reply_text: string;
  proposed_questions?: string[] | null;
  estimate: EstimateResponse | null;
  state: Record<string, unknown>;
  // UI Contract Extension (S2-A) - all optional for backward compatibility
  choices?: ChoicesConfig | null;
  step_info?: StepInfo | null;
  summary_patch?: SummaryPatch | null;
  ui_hint?: UIHint | null;
};

type SlotAvailability = {
  date: string;
  duration_minutes: number;
  slots: string[];
};

const STORAGE_KEY = 'economy_chat_session_id';
const UTM_STORAGE_KEY = 'economy_utm_params';
const REFERRER_STORAGE_KEY = 'economy_referrer';
const REFERRAL_CODE_KEY = 'economy_referral_code';

const packages = [
  {
    name: 'Small',
    label: 'S',
    beds: 'Studio / 1 bed',
    hours: '3.0 cleaner-hours',
    note: 'Great for apartments and light resets.'
  },
  {
    name: 'Medium',
    label: 'M',
    beds: '2 bed / 1-2 bath',
    hours: '3.5-5.0 cleaner-hours',
    note: 'Our most common Edmonton package.'
  },
  {
    name: 'Large',
    label: 'L',
    beds: '3 bed / 2 bath',
    hours: '5.5-7.0 cleaner-hours',
    note: 'Perfect for families and busy schedules.'
  },
  {
    name: 'Extra Large',
    label: 'XL',
    beds: '4+ bed / 3 bath',
    hours: '7.5+ cleaner-hours',
    note: 'Bigger homes or move-outs with a team.'
  }
];

const includedItems = [
  'Floors vacuumed and mopped',
  'Kitchen counters, sink, and exterior appliances wiped',
  'Bathrooms scrubbed and disinfected',
  'Dusting on reachable surfaces',
  'Trash removal and tidy reset'
];

const addonItems = [
  { name: 'Inside oven', price: '$30' },
  { name: 'Inside fridge', price: '$20' },
  { name: 'Inside microwave', price: '$10' },
  { name: 'Inside kitchen cabinets (up to 10)', price: '$40' },
  { name: 'Interior windows (up to 5)', price: '$30' },
  { name: 'Balcony basic sweep', price: '$25' },
  { name: 'Bed linen change (per bed)', price: '$10' },
  { name: 'Steam armchair', price: '$45' },
  { name: 'Steam sofa (2-seat)', price: '$90' },
  { name: 'Steam sofa (3-seat)', price: '$110' },
  { name: 'Steam sectional', price: '$150' },
  { name: 'Steam mattress', price: '$110' },
  { name: 'Carpet spot treatment', price: '$35' }
];

const faqs = [
  {
    q: 'How do you price cleaning?',
    a: 'We calculate cleaner-hours deterministically from your beds, baths, cleaning type, and add-ons. No dynamic or AI pricing.'
  },
  {
    q: 'Is there a minimum booking?',
    a: 'Yes. Economy cleanings start at 3.0 cleaner-hours, billed in 0.5 hour increments.'
  },
  {
    q: 'Can I book recurring service?',
    a: 'Weekly and biweekly schedules qualify for labor-only discounts. One-time and monthly stays at standard rates.'
  }
];

function formatSummaryValue(value: string | number | boolean | null): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (typeof value === 'number') return String(value);
  if (typeof value === 'string') return value;
  return '—';
}

export default function HomePage() {
  const [sessionId, setSessionId] = useState('');
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [messageInput, setMessageInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [proposedQuestions, setProposedQuestions] = useState<string[]>([]);
  const [estimate, setEstimate] = useState<EstimateResponse | null>(null);
  const [structuredInputs, setStructuredInputs] = useState<Record<string, unknown>>({});
  const [showLeadForm, setShowLeadForm] = useState(false);
  const [leadSubmitting, setLeadSubmitting] = useState(false);
  const [leadError, setLeadError] = useState<string | null>(null);
  const [leadSuccess, setLeadSuccess] = useState(false);
  const [issuedReferralCode, setIssuedReferralCode] = useState<string | null>(null);
  const [copyStatus, setCopyStatus] = useState<string | null>(null);
  const [leadForm, setLeadForm] = useState({
    name: '',
    phone: '',
    email: '',
    postal_code: '',
    address: '',
    preferred_dates: ['', '', ''],
    access_notes: '',
    parking: '',
    pets: '',
    allergies: '',
    notes: '',
    referral_code: ''
  });
  const [utmParams, setUtmParams] = useState<Record<string, string>>({});
  const [referrer, setReferrer] = useState<string | null>(null);
  const [slotsByDate, setSlotsByDate] = useState<SlotAvailability[]>([]);
  const [slotsLoading, setSlotsLoading] = useState(false);
  const [slotsError, setSlotsError] = useState<string | null>(null);
  const [selectedSlot, setSelectedSlot] = useState<string | null>(null);
  const [bookingSubmitting, setBookingSubmitting] = useState(false);
  const [bookingSuccess, setBookingSuccess] = useState<string | null>(null);
  const [bookingError, setBookingError] = useState<string | null>(null);

  // UI Contract Extension State (S2-A)
  const [choices, setChoices] = useState<ChoicesConfig | null>(null);
  const [stepInfo, setStepInfo] = useState<StepInfo | null>(null);
  const [summaryPatch, setSummaryPatch] = useState<SummaryPatch | null>(null);
  const [uiHint, setUIHint] = useState<UIHint | null>(null);
  const [selectedChoices, setSelectedChoices] = useState<string[]>([]);

  const apiBaseUrl = useMemo(
    () => process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000',
    []
  );

  const sessionReady = sessionId.length > 0;

  const copyReferralCode = useCallback(async () => {
    if (!issuedReferralCode || typeof navigator === 'undefined' || !navigator.clipboard) {
      return;
    }
    try {
      await navigator.clipboard.writeText(issuedReferralCode);
      setCopyStatus('Copied!');
    } catch (error) {
      setCopyStatus('Copy failed');
    }

    setTimeout(() => setCopyStatus(null), 2000);
  }, [issuedReferralCode]);

  useEffect(() => {
    if (typeof window === 'undefined') {
      return;
    }
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored) {
      setSessionId(stored);
      return;
    }
    const nextId = window.crypto.randomUUID();
    window.localStorage.setItem(STORAGE_KEY, nextId);
    setSessionId(nextId);
  }, []);

  useEffect(() => {
    if (typeof window === 'undefined') {
      return;
    }
    const storedUtm = window.localStorage.getItem(UTM_STORAGE_KEY);
    const storedReferrer = window.localStorage.getItem(REFERRER_STORAGE_KEY);
    const storedReferralCode = window.localStorage.getItem(REFERRAL_CODE_KEY);
    const storedValues = storedUtm ? (JSON.parse(storedUtm) as Record<string, string>) : {};

    const params = new URLSearchParams(window.location.search);
    const utmFields = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content'];
    const values: Record<string, string> = { ...storedValues };
    utmFields.forEach((field) => {
      const value = params.get(field);
      if (value) {
        values[field] = value;
      }
    });

    if (Object.keys(values).length > 0) {
      window.localStorage.setItem(UTM_STORAGE_KEY, JSON.stringify(values));
    }
    setUtmParams(values);

    const referralFromUrl = params.get('referral') || params.get('ref');
    const nextReferralCode = referralFromUrl || storedReferralCode;
    if (nextReferralCode) {
      window.localStorage.setItem(REFERRAL_CODE_KEY, nextReferralCode);
      setLeadForm((prev) => ({ ...prev, referral_code: nextReferralCode }));
    }

    const nextReferrer = document.referrer || storedReferrer;
    if (nextReferrer) {
      window.localStorage.setItem(REFERRER_STORAGE_KEY, nextReferrer);
    }
    setReferrer(nextReferrer || null);
  }, []);

  const loadSlots = useCallback(async () => {
    if (!estimate) {
      setSlotsByDate([]);
      setSelectedSlot(null);
      return;
    }
    setSlotsLoading(true);
    setSlotsError(null);
    setBookingSuccess(null);
    setBookingError(null);
    setSelectedSlot(null);
    try {
      const upcomingDates = getNextThreeDates();
      const responses = await Promise.all(
        upcomingDates.map(async (day) => {
          const params = new URLSearchParams({
            date: day,
            time_on_site_hours: String(estimate.time_on_site_hours)
          });
          if (leadForm.postal_code) {
            params.append('postal_code', leadForm.postal_code);
          }
          const response = await fetch(`${apiBaseUrl}/v1/slots?${params.toString()}`);
          if (!response.ok) {
            const text = await response.text();
            throw new Error(text || `Failed to load slots for ${day}`);
          }
          const payload = (await response.json()) as SlotAvailability;
          return payload;
        })
      );
      setSlotsByDate(responses);
      const firstAvailable = responses.find((entry) => entry.slots.length > 0)?.slots[0];
      setSelectedSlot(firstAvailable ?? null);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Unable to load slots';
      setSlotsError(message);
    } finally {
      setSlotsLoading(false);
    }
  }, [apiBaseUrl, estimate, leadForm.postal_code]);

  useEffect(() => {
    void loadSlots();
  }, [loadSlots]);

  const submitMessage = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || !sessionReady) {
        return;
      }
      setMessages((prev) => [...prev, { role: 'user', text: trimmed }]);
      setMessageInput('');
      setLoading(true);
      setError(null);

      try {
        const response = await fetch(`${apiBaseUrl}/v1/chat/turn`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json'
          },
          body: JSON.stringify({
            session_id: sessionId,
            message: trimmed,
            brand: 'economy',
            channel: 'web',
            client_context: {
              tz: 'America/Edmonton',
              locale: 'en-CA'
            }
          })
        });

        if (!response.ok) {
          const errorText = await response.text();
          throw new Error(errorText || `Request failed: ${response.status}`);
        }

        const data = (await response.json()) as ChatTurnResponse;
        setMessages((prev) => [...prev, { role: 'bot', text: data.reply_text }]);
        setProposedQuestions(data.proposed_questions ?? []);
        setEstimate(data.estimate ?? null);
        setStructuredInputs(data.state ?? {});

        // UI Contract Extension (S2-A) - handle new optional fields
        setChoices(data.choices ?? null);
        setStepInfo(data.step_info ?? null);
        setSummaryPatch(data.summary_patch ?? null);
        setUIHint(data.ui_hint ?? null);
        setSelectedChoices([]); // Reset selections on new message

        if (data.estimate) {
          setShowLeadForm(false);
          setLeadSuccess(false);
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Unexpected error';
        setError(message);
      } finally {
        setLoading(false);
      }
    },
    [apiBaseUrl, sessionId, sessionReady]
  );

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void submitMessage(messageInput);
  };

  const handleLeadFieldChange = (field: string, value: string, index?: number) => {
    setLeadForm((prev) => {
      if (field === 'preferred_dates' && typeof index === 'number') {
        const nextDates = [...prev.preferred_dates];
        nextDates[index] = value;
        return { ...prev, preferred_dates: nextDates };
      }
      return { ...prev, [field]: value };
    });
  };

  const submitLead = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!estimate) {
      setLeadError('Please request an estimate before booking.');
      return;
    }
    setLeadSubmitting(true);
    setLeadError(null);
    setIssuedReferralCode(null);
    try {
      const normalizedReferralCode = leadForm.referral_code.trim().toUpperCase();
      const payload = {
        name: leadForm.name,
        phone: leadForm.phone,
        email: leadForm.email || undefined,
        postal_code: leadForm.postal_code || undefined,
        address: leadForm.address || undefined,
        preferred_dates: leadForm.preferred_dates.filter((value) => value.trim().length > 0),
        access_notes: leadForm.access_notes || undefined,
        parking: leadForm.parking || undefined,
        pets: leadForm.pets || undefined,
        allergies: leadForm.allergies || undefined,
        notes: leadForm.notes || undefined,
        structured_inputs: structuredInputs,
        estimate_snapshot: estimate,
        ...utmParams,
        referrer,
        referral_code: normalizedReferralCode || undefined
      };

      const response = await fetch(`${apiBaseUrl}/v1/leads`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(payload)
      });

      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(errorText || `Request failed: ${response.status}`);
      }

      const leadResponse = (await response.json()) as { lead_id: string; referral_code?: string };

      setLeadSuccess(true);
      setShowLeadForm(false);
      setIssuedReferralCode(leadResponse.referral_code ?? null);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Unexpected error';
      setLeadError(message);
    } finally {
      setLeadSubmitting(false);
    }
  };

  const bookSelectedSlot = useCallback(async () => {
    if (!estimate || !selectedSlot) {
      setBookingError('Please select a slot to book.');
      return;
    }
    setBookingSubmitting(true);
    setBookingError(null);
    setBookingSuccess(null);
    try {
      const response = await fetch(`${apiBaseUrl}/v1/bookings`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          starts_at: selectedSlot,
          time_on_site_hours: estimate.time_on_site_hours,
          lead_id: undefined
        })
      });

      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || `Booking failed: ${response.status}`);
      }

      const booking = (await response.json()) as { booking_id: string; starts_at: string };
      setBookingSuccess(`Booked slot for ${formatSlotTime(booking.starts_at)}`);
      await loadSlots();
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Unexpected booking error';
      setBookingError(message);
    } finally {
      setBookingSubmitting(false);
    }
  }, [apiBaseUrl, estimate, loadSlots, selectedSlot]);

  return (
    <div className="page">
      <header className="site-header">
        <div className="brand">
          <p className="eyebrow">Economy Cleaning</p>
          <div className="brand-row">
            <span className="logo">EC</span>
            <div>
              <h1>Edmonton cleaning, priced upfront.</h1>
              <p className="muted">Deterministic quotes. Local team. Zero hidden fees.</p>
            </div>
          </div>
        </div>
        <a className="btn btn-primary" href="#chat">
          Start chat
        </a>
      </header>

      <main className="content">
        <section className="hero" aria-labelledby="hero-title">
          <div className="hero-copy">
            <p className="eyebrow">Honest pricing · Real availability</p>
            <h2 id="hero-title">Honest cleaning in Edmonton. $35 per cleaner-hour.</h2>
            <p className="subtitle">
              Tell us about your home and we will quote instantly with deterministic pricing. Book a slot in minutes and get a
              follow-up from a real dispatcher.
            </p>
            <div className="hero-actions">
              <a className="btn btn-primary" href="#chat">
                Start chat
              </a>
              <a className="btn btn-secondary" href="#packages">
                See packages
              </a>
            </div>
            <div className="pill-row">
              <span className="pill">Flat $35/hr labor</span>
              <span className="pill">0.5 hr rounding</span>
              <span className="pill">Edmonton-based team</span>
            </div>
            <ul className="trust-list">
              <li>Invoice-style estimates with every assumption spelled out.</li>
              <li>Next 3 days of availability shown instantly after you chat.</li>
              <li>No surge pricing, upsells, or hidden add-on fees.</li>
            </ul>
          </div>
          <div className="hero-card card">
            <div className="card-header">
              <div>
                <p className="eyebrow">Estimator preview</p>
                <h3>Ask the bot anything</h3>
              </div>
              <span className="pill pill-success">Live</span>
            </div>
            <div className="hero-card-body">
              <p className="label">Example prompt</p>
              <p className="example">“Deep clean for 2 bed 2 bath, oven + fridge.”</p>
              <p className="muted">You will get a deterministic estimate below once you start chatting.</p>
            </div>
            <div className="hero-metrics">
              <div>
                <p className="metric">3.0+</p>
                <p className="muted">minimum cleaner-hours</p>
              </div>
              <div>
                <p className="metric">1–3</p>
                <p className="muted">cleaner team size</p>
              </div>
              <div>
                <p className="metric">Weekly/biweekly discounts</p>
                <p className="muted">applied automatically</p>
              </div>
            </div>
          </div>
        </section>

        <section className="section" aria-labelledby="how-title">
          <div className="section-heading">
            <h2 id="how-title">How it works</h2>
            <p className="muted">Transparent steps from quote to clean.</p>
          </div>
          <div className="grid-3">
            <div className="step-card card">
              <span className="step-number">1</span>
              <h3>Tell us about your home</h3>
              <p>Share beds, baths, cleaning type, and add-ons. The bot captures the details.</p>
            </div>
            <div className="step-card card">
              <span className="step-number">2</span>
              <h3>Get an instant quote</h3>
              <p>Pricing is deterministic from our Economy config: $35 per cleaner-hour, no exceptions.</p>
            </div>
            <div className="step-card card">
              <span className="step-number">3</span>
              <h3>Book in minutes</h3>
              <p>Pick your preferred dates and we will confirm with a cleaner match from our Edmonton team.</p>
            </div>
          </div>
        </section>

        <section className="section" id="packages" aria-labelledby="packages-title">
          <div className="section-heading">
            <h2 id="packages-title">Packages</h2>
            <p className="muted">Cleaner-hours scale by home size. We bill exact hours used.</p>
          </div>
          <div className="package-grid">
            {packages.map((pkg) => (
              <article key={pkg.label} className="package-card card">
                <div className="package-top">
                  <span className="package-label">{pkg.label}</span>
                  <h3>{pkg.name}</h3>
                </div>
                <p className="muted">{pkg.beds}</p>
                <p className="package-hours">{pkg.hours}</p>
                <p>{pkg.note}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="section" aria-labelledby="included-title">
          <div className="section-heading">
            <h2 id="included-title">What’s included</h2>
            <p className="muted">Economy clean covers the essentials. Add extras as needed.</p>
          </div>
          <ul className="included-list">
            {includedItems.map((item) => (
              <li key={item} className="card">
                <span className="checkmark" aria-hidden>
                  ✓
                </span>
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </section>

        <section className="section" aria-labelledby="addons-title">
          <div className="section-heading">
            <h2 id="addons-title">Add-ons</h2>
            <p className="muted">Fixed prices on top of labor. Choose only what you need.</p>
          </div>
          <div className="addon-grid">
            {addonItems.map((addon) => (
              <div key={addon.name} className="addon-row card">
                <span>{addon.name}</span>
                <span className="addon-price">{addon.price}</span>
              </div>
            ))}
          </div>
        </section>

        <section className="section chat-wrapper" id="chat" aria-labelledby="chat-title">
          <div className="section-heading">
            <h2 id="chat-title">Start your booking</h2>
            <p className="muted">Chat with our bot to collect details, then confirm a slot.</p>
          </div>
          <div className="panel-grid">
            <div className="card chat-card">
              <div className="card-header">
                <div>
                  <p className="eyebrow">Conversation</p>
                  <h3>Chat with the estimator</h3>
                </div>
                <span className="pill">Session live</span>
              </div>
              <div className="card-body">
                {error ? <p className="alert alert-error">{error}</p> : null}
                <div className="chat-window">
                  {messages.length === 0 ? (
                    <p className="empty-state">Ask anything about your home to get started.</p>
                  ) : (
                    <ul className="messages">
                      {messages.map((message, index) => (
                        <li key={index} className={`message ${message.role}`}>
                          <span className="message-role">{message.role === 'user' ? 'You' : 'Bot'}</span>
                          <p className="message-text">{message.text}</p>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                <form className="composer" onSubmit={handleSubmit}>
                  <input
                    type="text"
                    placeholder="Type your message..."
                    value={messageInput}
                    onChange={(event) => setMessageInput(event.target.value)}
                    disabled={loading || !sessionReady}
                  />
                  <button className="btn btn-primary" type="submit" disabled={loading || !sessionReady || !messageInput.trim()}>
                    {loading ? 'Sending...' : 'Send'}
                  </button>
                </form>

                {/* UI Contract Extension: Step Progress Indicator (S2-A) - Using StepProgress component */}
                {stepInfo && (uiHint?.show_progress ?? true) ? (
                  <StepProgress
                    currentStep={stepInfo.current_step}
                    totalSteps={stepInfo.total_steps}
                    remaining={stepInfo.remaining_questions ?? undefined}
                  />
                ) : null}

                {/* UI Contract Extension: Choices (S2-A) - Using QuickChips component */}
                {choices && (uiHint?.show_choices ?? true) ? (
                  <div className="choices-section">
                    <div className="choices-header">
                      <p className="label">Select {choices.multi_select ? 'options' : 'an option'}</p>
                    </div>
                    <QuickChips
                      options={choices.items.map((choice) => ({
                        id: choice.id,
                        label: choice.label,
                        disabled: loading,
                      }))}
                      mode={choices.multi_select ? 'multi' : 'single'}
                      selected={selectedChoices}
                      onChange={(selected) => {
                        setSelectedChoices(selected);
                        // For single-select, submit immediately
                        if (!choices.multi_select && selected.length > 0) {
                          const choice = choices.items.find((c) => c.id === selected[0]);
                          if (choice) {
                            void submitMessage(choice.value ?? choice.label);
                          }
                        }
                      }}
                    />
                    {choices.multi_select && selectedChoices.length > 0 ? (
                      <PrimaryCTA
                        label="Confirm Selection"
                        onClick={() => {
                          const selected = choices.items
                            .filter((c) => selectedChoices.includes(c.id))
                            .map((c) => c.value ?? c.label)
                            .join(', ');
                          void submitMessage(selected);
                        }}
                        disabled={loading}
                      />
                    ) : null}
                  </div>
                ) : null}

                {/* Proposed questions - only shown if choices are not rendered (choices takes precedence) */}
                {proposedQuestions.length > 0 && !(choices && (uiHint?.show_choices ?? true)) ? (
                  <div className="quick-replies">
                    <div className="quick-reply-heading">
                      <p className="label">Quick replies</p>
                      <p className="muted">Tap to prefill</p>
                    </div>
                    <div className="quick-reply-list">
                      {proposedQuestions.map((question) => (
                        <button
                          key={question}
                          type="button"
                          className="btn btn-ghost quick-reply"
                          onClick={() => setMessageInput(question)}
                          disabled={loading}
                        >
                          {question}
                        </button>
                      ))}
                    </div>
                  </div>
                ) : null}
              </div>
              <div className="card-footer">
                <span className="muted">Session ID: {sessionId || 'Generating...'}</span>
                <span className="muted">API: {apiBaseUrl}</span>
              </div>
            </div>

            <div className="stack">
              <div className="card estimate-card">
                <div className="card-header">
                  <div>
                    <p className="eyebrow">Estimate snapshot</p>
                    <h3>Invoice-style quote</h3>
                  </div>
                  {estimate ? <span className="pill">Ready to book</span> : <span className="pill">Waiting for chat</span>}
                </div>
                {estimate ? (
                  <div className="card-body">
                    <div className="estimate-summary">
                      <div>
                        <p className="label">Team size</p>
                        <p className="value">{estimate.team_size}</p>
                      </div>
                      <div>
                        <p className="label">Time on site</p>
                        <p className="value">{estimate.time_on_site_hours} hrs</p>
                      </div>
                      <div>
                        <p className="label">Billed hours</p>
                        <p className="value">{estimate.billed_cleaner_hours} hrs</p>
                      </div>
                    </div>

                    <div className="estimate-lines">
                      <div className="line">
                        <span>Labor</span>
                        <span>{formatCurrency(estimate.labor_cost)}</span>
                      </div>
                      <div className="line">
                        <span>Add-ons</span>
                        <span>{formatCurrency(estimate.add_ons_cost)}</span>
                      </div>
                      <div className="line">
                        <span>Discounts</span>
                        <span>-{formatCurrency(estimate.discount_amount)}</span>
                      </div>
                      <div className="line total">
                        <div>
                          <p>Total before tax</p>
                          <p className="muted">Config: {estimate.pricing_config_id} {estimate.pricing_config_version}</p>
                        </div>
                        <p className="total-amount">{formatCurrency(estimate.total_before_tax)}</p>
                      </div>
                      <p className="muted mono">{estimate.config_hash}</p>
                    </div>

                    {estimate.breakdown ? (
                      <details className="estimate-breakdown">
                        <summary className="label">See calculation details</summary>
                        <pre className="mono">{JSON.stringify(estimate.breakdown, null, 2)}</pre>
                      </details>
                    ) : null}
                  </div>
                ) : (
                  <div className="card-body">
                    <p className="muted">Start the chat to generate your personalized estimate.</p>
                  </div>
                )}
              </div>

              {/* UI Contract Extension: Summary Patch (S2-A) - Using SummaryCard component */}
              {summaryPatch && (uiHint?.show_summary ?? true) ? (
                <div style={{ marginBottom: '20px' }}>
                  <SummaryCard
                    title={summaryPatch.title ?? 'Review Your Details'}
                    fields={summaryPatch.fields.map((field): SummaryFieldData => ({
                      id: field.key,
                      label: field.label,
                      value: field.value,
                      type: field.field_type ?? 'text',
                      options: field.options?.map((opt) => ({
                        value: opt.value ?? opt.label,
                        label: opt.label,
                      })),
                      editable: field.editable ?? false,
                    }))}
                    onSave={(updates) => {
                      // Send all updates as a batch message
                      const updateMessages = Object.entries(updates)
                        .map(([key, value]) => `${key}: ${value}`)
                        .join(', ');
                      void submitMessage(`Update details: ${updateMessages}`);
                    }}
                    showActions={true}
                  />
                  {uiHint?.show_confirm ? (
                    <div style={{ marginTop: '16px', display: 'flex', justifyContent: 'center' }}>
                      <PrimaryCTA
                        label="Confirm Details"
                        onClick={() => void submitMessage('Confirm details')}
                        disabled={loading}
                      />
                    </div>
                  ) : null}
                </div>
              ) : null}

              <div className="card slots-card">
                <div className="slots-header">
                  <div>
                    <p className="label">Book a time</p>
                    <p className="muted">
                      Next 3 days · 30 minute steps · Times in America/Edmonton · {estimate?.time_on_site_hours ?? '—'} hours on
                      site
                    </p>
                  </div>
                  <button type="button" className="btn btn-ghost" onClick={() => void loadSlots()} disabled={slotsLoading}>
                    {slotsLoading ? 'Refreshing...' : 'Refresh'}
                  </button>
                </div>
                <div className="card-body">
                  {slotsError ? <p className="alert alert-error">{slotsError}</p> : null}
                  {slotsLoading ? <p className="muted">Loading slots...</p> : null}
                  {!slotsLoading && slotsByDate.length === 0 ? (
                    <p className="muted">Slots will appear after your estimate.</p>
                  ) : null}
                  {!slotsLoading && slotsByDate.length > 0 ? (
                    <div className="slot-grid">
                      {slotsByDate.map((day) => (
                        <div key={day.date} className="slot-column card">
                          <p className="label">{formatSlotDateHeading(day.date)}</p>
                          <div className="slot-list">
                            {day.slots.length === 0 ? (
                              <p className="muted">No openings</p>
                            ) : (
                              day.slots.map((slot) => (
                                <button
                                  key={slot}
                                  type="button"
                                  className={`slot-button ${selectedSlot === slot ? 'selected' : ''}`}
                                  onClick={() => setSelectedSlot(slot)}
                                >
                                  {formatSlotTime(slot)}
                                </button>
                              ))
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : null}
                  <div className="booking-actions">
                    <button
                      type="button"
                      className="btn btn-primary"
                      onClick={() => void bookSelectedSlot()}
                      disabled={bookingSubmitting || !selectedSlot || slotsLoading}
                    >
                      {bookingSubmitting ? 'Booking...' : 'Book selected time'}
                    </button>
                    {bookingSuccess ? <p className="alert alert-success">{bookingSuccess}</p> : null}
                    {bookingError ? <p className="alert alert-error">{bookingError}</p> : null}
                  </div>
                </div>
              </div>

              {estimate ? (
                <section className="card lead-cta">
                  <div className="card-header">
                    <div>
                      <p className="eyebrow">Optional</p>
                      <h3>Share details for dispatcher follow-up</h3>
                    </div>
                    {!showLeadForm ? (
                      <button className="btn btn-secondary" type="button" onClick={() => setShowLeadForm(true)}>
                        Add your info
                      </button>
                    ) : null}
                  </div>
                  {!showLeadForm && !leadSuccess ? (
                    <div className="card-body">
                      <p className="muted">Drop your contact info so we can confirm or adjust based on your preferences.</p>
                      <div className="lead-actions">
                        <button className="btn btn-primary" type="button" onClick={() => setShowLeadForm(true)}>
                          Submit booking request
                        </button>
                      </div>
                    </div>
                  ) : null}

                  {leadSuccess ? (
                    <div className="card-body lead-confirmation">
                      <p className="eyebrow">Submitted</p>
                      <p className="value">We received your request.</p>
                      <p className="muted">A dispatcher will follow up by phone or email shortly.</p>
                      {issuedReferralCode ? (
                        <div className="referral-box">
                          <div>
                            <p className="label">Referral code</p>
                            <p className="value">{issuedReferralCode}</p>
                            <p className="muted">Share with friends for credits.</p>
                          </div>
                          <button className="btn btn-secondary" type="button" onClick={() => void copyReferralCode()}>
                            Copy code
                          </button>
                          {copyStatus ? <p className="muted">{copyStatus}</p> : null}
                        </div>
                      ) : null}
                    </div>
                  ) : null}

                  {showLeadForm && !leadSuccess ? (
                    <form className="card-body lead-form" onSubmit={submitLead}>
                      <div className="form-grid">
                        <label>
                          <span>Full name *</span>
                          <input
                            type="text"
                            required
                            value={leadForm.name}
                            onChange={(event) => handleLeadFieldChange('name', event.target.value)}
                          />
                        </label>
                        <label>
                          <span>Phone *</span>
                          <input
                            type="tel"
                            required
                            value={leadForm.phone}
                            onChange={(event) => handleLeadFieldChange('phone', event.target.value)}
                          />
                        </label>
                        <label>
                          <span>Email</span>
                          <input
                            type="email"
                            value={leadForm.email}
                            onChange={(event) => handleLeadFieldChange('email', event.target.value)}
                          />
                        </label>
                        <label>
                          <span>Postal code</span>
                          <input
                            type="text"
                            value={leadForm.postal_code}
                            onChange={(event) => handleLeadFieldChange('postal_code', event.target.value)}
                          />
                        </label>
                        <label className="full">
                          <span>Address</span>
                          <input
                            type="text"
                            value={leadForm.address}
                            onChange={(event) => handleLeadFieldChange('address', event.target.value)}
                          />
                        </label>
                      </div>

                      <div className="form-grid">
                        {leadForm.preferred_dates.map((value, index) => (
                          <label key={`date-${index}`}>
                            <span>Preferred date option {index + 1}</span>
                            <input
                              type="text"
                              placeholder="Sat afternoon"
                              value={value}
                              onChange={(event) => handleLeadFieldChange('preferred_dates', event.target.value, index)}
                            />
                          </label>
                        ))}
                      </div>

                      <div className="form-grid">
                        <label className="full">
                          <span>Access notes</span>
                          <input
                            type="text"
                            value={leadForm.access_notes}
                            onChange={(event) => handleLeadFieldChange('access_notes', event.target.value)}
                          />
                        </label>
                        <label className="full">
                          <span>Parking</span>
                          <input
                            type="text"
                            value={leadForm.parking}
                            onChange={(event) => handleLeadFieldChange('parking', event.target.value)}
                          />
                        </label>
                        <label>
                          <span>Pets</span>
                          <input
                            type="text"
                            value={leadForm.pets}
                            onChange={(event) => handleLeadFieldChange('pets', event.target.value)}
                          />
                        </label>
                        <label>
                          <span>Allergies</span>
                          <input
                            type="text"
                            value={leadForm.allergies}
                            onChange={(event) => handleLeadFieldChange('allergies', event.target.value)}
                          />
                        </label>
                        <label>
                          <span>Referral code</span>
                          <input
                            type="text"
                            value={leadForm.referral_code}
                            onChange={(event) => handleLeadFieldChange('referral_code', event.target.value.toUpperCase())}
                            placeholder="ABC12345"
                          />
                        </label>
                        <label className="full">
                          <span>Notes</span>
                          <textarea
                            value={leadForm.notes}
                            onChange={(event) => handleLeadFieldChange('notes', event.target.value)}
                          />
                        </label>
                      </div>

                      {leadError ? <p className="alert alert-error">{leadError}</p> : null}
                      <button className="btn btn-primary" type="submit" disabled={leadSubmitting}>
                        {leadSubmitting ? 'Submitting...' : 'Submit booking request'}
                      </button>
                    </form>
                  ) : null}
                </section>
              ) : null}
            </div>
          </div>
        </section>

        <section className="section" aria-labelledby="faq-title">
          <h2 id="faq-title">FAQ</h2>
          <div className="faq-list">
            {faqs.map((faq) => (
              <details key={faq.q} className="card">
                <summary>{faq.q}</summary>
                <p>{faq.a}</p>
              </details>
            ))}
          </div>
        </section>

        <section className="cta card" aria-labelledby="cta-title">
          <div>
            <h2 id="cta-title">Ready for a cleaner home?</h2>
            <p className="subtitle">Start the chat to get a deterministic quote and book your preferred time.</p>
          </div>
          <a className="btn btn-primary" href="#chat">
            Start chat
          </a>
        </section>
      </main>

      {/* Always-visible handoff button */}
      <AlwaysVisibleHandoff
        onHandoff={() => {
          // Handle human handoff - could send a message to the bot or open a contact form
          void submitMessage('I would like to speak with a human');
        }}
        label="Call a human"
      />
    </div>
  );
}

function formatCurrency(amount: number) {
  return new Intl.NumberFormat('en-CA', {
    style: 'currency',
    currency: 'CAD',
    maximumFractionDigits: 2
  }).format(amount);
}

function formatYMDInTz(date: Date, timeZone: string): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit'
  }).formatToParts(date);

  const lookup = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${lookup.year}-${lookup.month}-${lookup.day}`;
}

function getNextThreeDates(): string[] {
  const today = new Date();
  return Array.from({ length: 3 }).map((_, index) => {
    const next = new Date(today);
    next.setDate(today.getDate() + index);
    return formatYMDInTz(next, 'America/Edmonton');
  });
}

function dateFromYMDInUtc(day: string): Date {
  const [year, month, dayOfMonth] = day.split('-').map(Number);
  return new Date(Date.UTC(year, month - 1, dayOfMonth, 12, 0, 0));
}

function formatSlotTime(slot: string): string {
  const date = new Date(slot);
  return date.toLocaleTimeString('en-CA', {
    hour: 'numeric',
    minute: '2-digit',
    timeZone: 'America/Edmonton',
    timeZoneName: 'short'
  });
}

function formatSlotDateHeading(day: string): string {
  const date = dateFromYMDInUtc(day);
  return date.toLocaleDateString('en-CA', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    timeZone: 'America/Edmonton'
  });
}
