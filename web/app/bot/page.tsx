'use client';

import { useEffect, useMemo, useState } from 'react';

type Progress = {
  current: number;
  total: number;
};

type BotReply = {
  text: string;
  intent: string;
  confidence: number;
  quickReplies: string[];
  progress?: Progress | null;
  summary: Record<string, unknown>;
};

type Message = {
  role: 'user' | 'bot';
  text: string;
};

export default function BotPlayground() {
  const apiBase = useMemo(() => process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000', []);
  const [conversationId, setConversationId] = useState<string>('');
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [quickReplies, setQuickReplies] = useState<string[]>([]);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [summary, setSummary] = useState<Record<string, unknown>>({});
  const [status, setStatus] = useState<string>('');
  const [creatingLead, setCreatingLead] = useState(false);

  useEffect(() => {
    const createSession = async () => {
      const response = await fetch(`${apiBase}/api/bot/session`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ channel: 'web' })
      });
      if (!response.ok) {
        setStatus('Unable to start session');
        return;
      }
      const data = await response.json();
      setConversationId(data.conversationId);
      setStatus('Ready');
    };

    void createSession();
  }, [apiBase]);

  const handleSend = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || !conversationId) return;
    setMessages((prev) => [...prev, { role: 'user', text: trimmed }]);
    setInput('');
    setStatus('Sending...');

    try {
      const response = await fetch(`${apiBase}/api/bot/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ conversationId, text: trimmed })
      });
      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(errorText || 'Message failed');
      }
      const payload = (await response.json()) as { reply: BotReply };
      const reply = payload.reply;
      setMessages((prev) => [...prev, { role: 'bot', text: reply.text }]);
      setQuickReplies(reply.quickReplies ?? []);
      setProgress(reply.progress ?? null);
      setSummary(reply.summary ?? {});
      setStatus('');
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unexpected error';
      setStatus(message);
    }
  };

  const createLead = async () => {
    if (!conversationId) return;
    setCreatingLead(true);
    setStatus('Submitting lead...');
    try {
      const response = await fetch(`${apiBase}/api/leads`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sourceConversationId: conversationId })
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || 'Lead failed');
      }
      setStatus('Lead created!');
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Lead creation failed';
      setStatus(message);
    } finally {
      setCreatingLead(false);
    }
  };

  return (
    <div style={{ maxWidth: 960, margin: '0 auto', padding: '24px', display: 'grid', gap: '16px' }}>
      <header>
        <p style={{ margin: 0, color: '#555' }}>BOT v1 playground</p>
        <h1 style={{ margin: '4px 0 0' }}>Chat + handoff tester</h1>
        <p style={{ margin: '4px 0 0', color: '#777' }}>
          Send a message, tap quick replies, and confirm a lead. Every escalation automatically opens a case.
        </p>
        <p style={{ color: status.includes('failed') ? '#b00020' : '#2f855a' }}>{status}</p>
      </header>

      <section style={{ border: '1px solid #e0e0e0', borderRadius: 8, padding: 16 }}>
        <div style={{ minHeight: 200, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {messages.map((message, index) => (
            <div
              key={`${message.role}-${index}`}
              style={{
                alignSelf: message.role === 'user' ? 'flex-end' : 'flex-start',
                background: message.role === 'user' ? '#eef2ff' : '#f7fafc',
                borderRadius: 12,
                padding: '8px 12px',
                maxWidth: '80%'
              }}
            >
              <strong style={{ display: 'block', marginBottom: 4 }}>{message.role === 'user' ? 'You' : 'Bot'}</strong>
              <span>{message.text}</span>
            </div>
          ))}
        </div>

        <form
          onSubmit={(event) => {
            event.preventDefault();
            void handleSend(input);
          }}
          style={{ marginTop: 12, display: 'flex', gap: 8 }}
        >
          <input
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="Type a message"
            style={{ flex: 1, padding: 10, borderRadius: 8, border: '1px solid #ccc' }}
          />
          <button
            type="submit"
            style={{ padding: '10px 16px', borderRadius: 8, border: 'none', background: '#2563eb', color: '#fff' }}
          >
            Send
          </button>
        </form>

        {quickReplies.length > 0 ? (
          <div style={{ marginTop: 12, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {quickReplies.map((reply) => (
              <button
                key={reply}
                type="button"
                onClick={() => void handleSend(reply)}
                style={{
                  padding: '8px 12px',
                  borderRadius: 16,
                  border: '1px solid #cbd5e1',
                  background: '#fff',
                  cursor: 'pointer'
                }}
              >
                {reply}
              </button>
            ))}
          </div>
        ) : null}
      </section>

      <section style={{ border: '1px solid #e0e0e0', borderRadius: 8, padding: 16, display: 'grid', gap: 8 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <h2 style={{ margin: 0 }}>Progress & summary</h2>
          {progress ? (
            <span style={{ fontSize: 14, color: '#374151' }}>
              Step {progress.current} of {progress.total}
            </span>
          ) : (
            <span style={{ fontSize: 14, color: '#9ca3af' }}>No progress yet</span>
          )}
        </div>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          {Object.entries(summary).length === 0 ? (
            <p style={{ margin: 0, color: '#6b7280' }}>No summary yet. Share details to fill this in.</p>
          ) : (
            Object.entries(summary).map(([key, value]) => (
              <div
                key={key}
                style={{
                  border: '1px solid #e5e7eb',
                  borderRadius: 8,
                  padding: 10,
                  minWidth: 160,
                  background: '#f9fafb'
                }}
              >
                <p style={{ margin: '0 0 4px', color: '#6b7280', fontSize: 12 }}>{key}</p>
                <p style={{ margin: 0, fontWeight: 600 }}>{String(value)}</p>
              </div>
            ))
          )}
        </div>
        <button
          type="button"
          onClick={() => void createLead()}
          disabled={creatingLead || !conversationId}
          style={{
            padding: '10px 16px',
            borderRadius: 8,
            border: 'none',
            background: creatingLead ? '#9ca3af' : '#16a34a',
            color: '#fff',
            cursor: creatingLead ? 'not-allowed' : 'pointer',
            width: 'fit-content'
          }}
        >
          {creatingLead ? 'Submitting...' : 'Confirm (create lead)'}
        </button>
      </section>
    </div>
  );
}
