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
  pricing_config_version: number;
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
                {estimate.pricing_config_id} v{estimate.pricing_config_version}
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
