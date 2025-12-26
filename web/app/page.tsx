'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';

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

type ChatTurnResponse = {
  reply_text: string;
  proposed_questions: string[];
  estimate: EstimateResponse | null;
  state: Record<string, unknown>;
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

  const apiBaseUrl = useMemo(
    () => process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000',
    []
  );

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
      if (!trimmed || !sessionId) {
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
    [apiBaseUrl, sessionId]
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
    <div className="landing">
      <header className="site-header">
        <div className="brand">
          <span className="badge">Economy Cleaning</span>
          <p className="eyebrow">Edmonton, Alberta</p>
        </div>
        <a className="primary" href="#chat">
          Start chat
        </a>
      </header>

      <main>
        <section className="hero" aria-labelledby="hero-title">
          <div className="hero-copy">
            <p className="eyebrow">Honest pricing. Real availability.</p>
            <h1 id="hero-title">Honest cleaning in Edmonton. $35 per cleaner-hour.</h1>
            <p className="subtitle">
              Economy is the straightforward clean. Tell us your home details and we will
              quote instantly with deterministic pricing and zero surprises.
            </p>
            <div className="hero-actions">
              <a className="primary" href="#chat">
                Start chat
              </a>
              <a className="ghost" href="#packages">
                See packages
              </a>
            </div>
            <div className="hero-metrics">
              <div>
                <p className="metric">3.0+</p>
                <p className="muted">minimum cleaner-hours</p>
              </div>
              <div>
                <p className="metric">0.5 hr</p>
                <p className="muted">rounding increment</p>
              </div>
              <div>
                <p className="metric">1–3</p>
                <p className="muted">cleaner team size</p>
              </div>
            </div>
          </div>
          <div className="hero-card">
            <h2>Instant estimate</h2>
            <p className="muted">
              Ask questions naturally. The bot turns your details into a fixed Economy
              quote.
            </p>
            <div className="hero-card-body">
              <p className="label">Example</p>
              <p className="example">“Deep clean for 2 bed 2 bath, oven + fridge.”</p>
              <p className="muted">Chat below to get your exact rate.</p>
            </div>
          </div>
        </section>

        <section className="section" aria-labelledby="how-title">
          <h2 id="how-title">How it works</h2>
          <div className="grid-3">
            <div className="step-card">
              <span className="step-number">1</span>
              <h3>Tell us about your home</h3>
              <p>
                Share beds, baths, cleaning type, and any add-ons. The bot captures the
                details.
              </p>
            </div>
            <div className="step-card">
              <span className="step-number">2</span>
              <h3>Get an instant quote</h3>
              <p>
                Pricing is deterministic from our Economy config: $35 per cleaner-hour,
                no exceptions.
              </p>
            </div>
            <div className="step-card">
              <span className="step-number">3</span>
              <h3>Book in minutes</h3>
              <p>
                Pick your preferred dates and we will confirm with a cleaner match from
                our Edmonton team.
              </p>
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
              <article key={pkg.label} className="package-card">
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
          <h2 id="included-title">What’s included</h2>
          <ul className="included-list">
            {includedItems.map((item) => (
              <li key={item}>{item}</li>
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
              <div key={addon.name} className="addon-row">
                <span>{addon.name}</span>
                <span className="addon-price">{addon.price}</span>
              </div>
            ))}
          </div>
        </section>

        <section className="section" aria-labelledby="faq-title">
          <h2 id="faq-title">FAQ</h2>
          <div className="faq-list">
            {faqs.map((faq) => (
              <details key={faq.q}>
                <summary>{faq.q}</summary>
                <p>{faq.a}</p>
              </details>
            ))}
          </div>
        </section>

        <section className="cta" aria-labelledby="cta-title">
          <div>
            <h2 id="cta-title">Ready for a cleaner home?</h2>
            <p className="subtitle">
              Start the chat to get a deterministic quote and book your preferred time.
            </p>
          </div>
          <a className="primary" href="#chat">
            Start chat
          </a>
        </section>

        <section className="chat-section" id="chat" aria-live="polite">
          <div className="chat-card">
            <div className="chat-header">
              <div>
                <p className="eyebrow">Economy chat</p>
                <h2>Instant quote chat</h2>
              </div>
              <span className="status">Live</span>
            </div>

            <div className="chat-window">
              {messages.length === 0 ? (
                <p className="empty-state">
                  Send a message to begin. Example: “Need a deep clean for 2 bed 2 bath with
                  oven and fridge.”
                </p>
              ) : (
                <ul className="messages">
                  {messages.map((msg, index) => (
                    <li key={`${msg.role}-${index}`} className={`message ${msg.role}`}>
                      <span className="message-role">
                        {msg.role === 'user' ? 'You' : 'Bot'}
                      </span>
                      <p>{msg.text}</p>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div className="card-body">
              {error ? <p className="error">{error}</p> : null}
              <form onSubmit={handleSubmit} className="composer">
                <input
                  type="text"
                  placeholder="Type your message..."
                  value={messageInput}
                  onChange={(event) => setMessageInput(event.target.value)}
                  disabled={loading}
                />
                <button type="submit" disabled={loading || !messageInput.trim()}>
                  {loading ? 'Sending...' : 'Send'}
                </button>
              </form>
              {proposedQuestions.length > 0 ? (
                <div className="quick-replies">
                  <p className="label">Quick replies</p>
                  <p className="muted">Tap to prefill</p>
                  <div className="quick-reply-list">
                    {proposedQuestions.map((question) => (
                      <button
                        key={question}
                        type="button"
                        className="quick-reply"
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

            {estimate ? (
              <section className="estimate">
                <h3>Estimate Snapshot</h3>
                <div className="estimate-grid">
                  <div>
                    <p className="label">Config</p>
                    <p className="mono">
                      {estimate.pricing_config_id} {estimate.pricing_config_version}
                    </p>
                    <p className="muted">{estimate.config_hash}</p>
                  </div>
                  <div>
                    <p className="label">Team Size</p>
                    <p className="value">{estimate.team_size}</p>
                  </div>
                  <div>
                    <p className="label">Time on site (hours)</p>
                    <p className="value">{estimate.time_on_site_hours}</p>
                  </div>
                  <div>
                    <p className="label">Labor Cost</p>
                    <p className="value">{formatCurrency(estimate.labor_cost)}</p>
                  </div>
                  <div>
                    <p className="label">Add-ons Cost</p>
                    <p className="value">{formatCurrency(estimate.add_ons_cost)}</p>
                  </div>
                  <div>
                    <p className="label">Discount</p>
                    <p className="value">-{formatCurrency(estimate.discount_amount)}</p>
                  </div>
                  <div>
                    <p className="label">Total Before Tax</p>
                    <p className="value total">{formatCurrency(estimate.total_before_tax)}</p>
                  </div>
                </div>
                {estimate.breakdown ? (
                  <div className="estimate-breakdown">
                    <p className="label">Debug breakdown</p>
                    <pre className="mono">{JSON.stringify(estimate.breakdown, null, 2)}</pre>
                  </div>
                ) : null}

                <div className="slots-card">
                  <div className="slots-header">
                    <div>
                      <p className="label">Book a time</p>
                      <p className="muted">
                        Next 3 days · 30 minute steps · {estimate.time_on_site_hours} hours on site · Times in America/Edmonton
                      </p>
                    </div>
                    <button type="button" className="ghost" onClick={() => void loadSlots()} disabled={slotsLoading}>
                      {slotsLoading ? 'Refreshing...' : 'Refresh'}
                    </button>
                  </div>
                  {slotsError ? <p className="error">{slotsError}</p> : null}
                  {slotsLoading ? <p className="muted">Loading slots...</p> : null}
                  {!slotsLoading && slotsByDate.length === 0 ? (
                    <p className="muted">Slots will appear after your estimate.</p>
                  ) : null}
                  {!slotsLoading && slotsByDate.length > 0 ? (
                    <div className="slot-grid">
                      {slotsByDate.map((day) => (
                        <div key={day.date} className="slot-column">
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
                      className="primary"
                      onClick={() => void bookSelectedSlot()}
                      disabled={bookingSubmitting || !selectedSlot || slotsLoading}
                    >
                      {bookingSubmitting ? 'Booking…' : selectedSlot ? `Book ${formatSlotTime(selectedSlot)}` : 'Select a slot'}
                    </button>
                    {bookingSuccess ? <p className="success">{bookingSuccess}</p> : null}
                    {bookingError ? <p className="error">{bookingError}</p> : null}
                  </div>
                </div>

                {leadSuccess ? (
                  <div className="lead-confirmation">
                    <h4>Request received</h4>
                    <p>
                      Thanks! We&apos;ve saved your booking request. Our team will confirm your
                      preferred times shortly.
                    </p>
                    {issuedReferralCode ? (
                      <div className="muted">
                        <p>
                          Your referral code: <strong>{issuedReferralCode}</strong>. Share it with
                          friends so both of you get credit when they book.
                        </p>
                        <div className="referral-actions" style={{ display: 'flex', gap: '0.5rem' }}>
                          <button
                            type="button"
                            className="secondary"
                            onClick={() => void copyReferralCode()}
                          >
                            Copy code
                          </button>
                          {copyStatus ? <span className="muted">{copyStatus}</span> : null}
                        </div>
                      </div>
                    ) : null}
                  </div>
                ) : (
                  <div className="lead-cta">
                    <button
                      type="button"
                      className="primary"
                      onClick={() => setShowLeadForm((prev) => !prev)}
                    >
                      {showLeadForm ? 'Hide booking form' : 'Book / Leave contact'}
                    </button>
                    <p className="muted">
                      Ready to book? Share your details and preferred dates.
                    </p>
                  </div>
                )}

                {showLeadForm ? (
                  <form className="lead-form" onSubmit={submitLead}>
                    <div className="form-grid">
                      <label>
                        <span>Name *</span>
                        <input
                          type="text"
                          value={leadForm.name}
                          onChange={(event) => handleLeadFieldChange('name', event.target.value)}
                          required
                        />
                      </label>
                      <label>
                        <span>Phone *</span>
                        <input
                          type="tel"
                          value={leadForm.phone}
                          onChange={(event) => handleLeadFieldChange('phone', event.target.value)}
                          required
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
                          onChange={(event) =>
                            handleLeadFieldChange('postal_code', event.target.value)
                          }
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
                            onChange={(event) =>
                              handleLeadFieldChange('preferred_dates', event.target.value, index)
                            }
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
                          onChange={(event) =>
                            handleLeadFieldChange('access_notes', event.target.value)
                          }
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
                          onChange={(event) =>
                            handleLeadFieldChange('allergies', event.target.value)
                          }
                        />
                      </label>
                      <label>
                        <span>Referral code</span>
                        <input
                          type="text"
                          value={leadForm.referral_code}
                          onChange={(event) =>
                            handleLeadFieldChange('referral_code', event.target.value.toUpperCase())
                          }
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

                    {leadError ? <p className="error">{leadError}</p> : null}
                    <button className="primary" type="submit" disabled={leadSubmitting}>
                      {leadSubmitting ? 'Submitting...' : 'Submit booking request'}
                    </button>
                  </form>
                ) : null}
              </section>
            ) : null}

            <div className="chat-footer">
              <span className="muted">Session ID: {sessionId || 'Generating...'}</span>
              <span className="muted">API: {apiBaseUrl}</span>
            </div>
          </div>
        </section>
      </main>
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
