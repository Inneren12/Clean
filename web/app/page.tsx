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

const STORAGE_KEY = 'economy_chat_session_id';

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
    notes: ''
  });
  const [utmParams, setUtmParams] = useState<Record<string, string>>({});
  const [referrer, setReferrer] = useState<string | null>(null);

  const apiBaseUrl = useMemo(
    () => process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000',
    []
  );

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
    const params = new URLSearchParams(window.location.search);
    const utmFields = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content'];
    const values: Record<string, string> = {};
    utmFields.forEach((field) => {
      const value = params.get(field);
      if (value) {
        values[field] = value;
      }
    });
    setUtmParams(values);
    setReferrer(document.referrer || null);
  }, []);

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
            message: trimmed
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

  const handleLeadFieldChange = (
    field: string,
    value: string,
    index?: number
  ) => {
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
    try {
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
        referrer
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

      setLeadSuccess(true);
      setShowLeadForm(false);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Unexpected error';
      setLeadError(message);
    } finally {
      setLeadSubmitting(false);
    }
  };

  return (
    <div className="card">
      <section className="card-body">
        <div className="session-row">
          <span className="label">Session ID</span>
          <span className="mono">{sessionId || 'Generating...'}</span>
        </div>
        <div className="session-row">
          <span className="label">API Base URL</span>
          <span className="mono">{apiBaseUrl}</span>
        </div>
      </section>

      <section className="chat-window">
        {messages.length === 0 ? (
          <p className="empty-state">
            Send a message to begin. Example: "Need a deep clean for 2 bed 2 bath with
            oven and fridge."
          </p>
        ) : (
          <ul className="messages">
            {messages.map((msg, index) => (
              <li key={`${msg.role}-${index}`} className={`message ${msg.role}`}>
                <span className="message-role">{msg.role === 'user' ? 'You' : 'Bot'}</span>
                <p>{msg.text}</p>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="card-body">
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
      </section>

      {estimate ? (
        <section className="estimate">
          <h2>Estimate Snapshot</h2>
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

          {leadSuccess ? (
            <div className="lead-confirmation">
              <h3>Request received</h3>
              <p>
                Thanks! We&apos;ve saved your booking request. Our team will confirm your
                preferred times shortly.
              </p>
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
