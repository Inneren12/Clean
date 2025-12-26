"use client";

import { useEffect, useMemo, useState } from "react";

const STORAGE_KEY = "admin_basic_token";
const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const EDMONTON_TZ = "America/Edmonton";

type Lead = {
  lead_id: string;
  name: string;
  email?: string | null;
  status?: string;
};

type Booking = {
  booking_id: string;
  lead_id?: string | null;
  starts_at: string;
  duration_minutes: number;
  status: string;
  lead_name?: string | null;
  lead_email?: string | null;
};

function formatDateTime(value: string) {
  const dt = new Date(value);
  return new Intl.DateTimeFormat("en-CA", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: EDMONTON_TZ,
  }).format(dt);
}

export default function AdminPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [token, setToken] = useState<string | null>(null);
  const [leads, setLeads] = useState<Lead[]>([]);
  const [bookings, setBookings] = useState<Booking[]>([]);
  const [leadStatusFilter, setLeadStatusFilter] = useState<string>("");
  const [selectedDate, setSelectedDate] = useState<string>(() => {
    const today = new Date();
    return today.toISOString().slice(0, 10);
  });
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored) {
      setToken(stored);
    }
  }, []);

  const authHeaders = useMemo<Record<string, string>>(() => {
    if (!token) return {} as Record<string, string>;
    return { Authorization: `Basic ${token}` };
  }, [token]);

  const loadLeads = async () => {
    if (!token) return;
    const filter = leadStatusFilter ? `?status=${encodeURIComponent(leadStatusFilter)}` : "";
    const response = await fetch(`${API_BASE}/v1/admin/leads${filter}`, {
      headers: authHeaders,
      cache: "no-store",
    });
    if (!response.ok) return;
    const data = (await response.json()) as Lead[];
    setLeads(data);
  };

  const loadBookings = async () => {
    if (!token) return;
    const response = await fetch(
      `${API_BASE}/v1/admin/bookings?from=${selectedDate}&to=${selectedDate}`,
      { headers: authHeaders, cache: "no-store" }
    );
    if (!response.ok) return;
    const data = (await response.json()) as Booking[];
    setBookings(data);
  };

  useEffect(() => {
    void loadLeads();
  }, [token, leadStatusFilter]);

  useEffect(() => {
    void loadBookings();
  }, [token, selectedDate]);

  const saveCredentials = () => {
    const encoded = btoa(`${username}:${password}`);
    setToken(encoded);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(STORAGE_KEY, encoded);
    }
    setMessage("Saved credentials");
  };

  const updateLeadStatus = async (leadId: string, status: string) => {
    setMessage(null);
    const response = await fetch(`${API_BASE}/v1/admin/leads/${leadId}/status`, {
      method: "POST",
      headers: { ...authHeaders, "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    });
    if (response.ok) {
      setMessage("Lead updated");
      void loadLeads();
    } else {
      setMessage("Failed to update lead");
    }
  };

  const performBookingAction = async (bookingId: string, action: "confirm" | "cancel") => {
    setMessage(null);
    const response = await fetch(`${API_BASE}/v1/admin/bookings/${bookingId}/${action}`, {
      method: "POST",
      headers: authHeaders,
    });
    if (response.ok) {
      setMessage(`Booking ${action}ed`);
      void loadBookings();
    } else {
      setMessage("Booking action failed");
    }
  };

  const rescheduleBooking = async (bookingId: string) => {
    const newStart = prompt("New start (ISO8601, local time accepted)");
    if (!newStart) return;
    const duration = prompt("Time on site hours", "1.5");
    if (!duration) return;
    const response = await fetch(`${API_BASE}/v1/admin/bookings/${bookingId}/reschedule`, {
      method: "POST",
      headers: { ...authHeaders, "Content-Type": "application/json" },
      body: JSON.stringify({ starts_at: newStart, time_on_site_hours: parseFloat(duration) }),
    });
    if (response.ok) {
      setMessage("Booking rescheduled");
      void loadBookings();
    } else {
      setMessage("Reschedule failed");
    }
  };

  const weekView = useMemo(() => {
    const start = new Date(selectedDate);
    const days: { label: string; date: string; items: Booking[] }[] = [];
    for (let i = 0; i < 7; i++) {
      const d = new Date(start);
      d.setDate(start.getDate() + i);
      const key = d.toISOString().slice(0, 10);
      days.push({
        label: d.toLocaleDateString("en-CA", { weekday: "short", month: "short", day: "numeric" }),
        date: key,
        items: bookings.filter((b) => b.starts_at.startsWith(key)),
      });
    }
    return days;
  }, [bookings, selectedDate]);

  return (
    <div style={{ padding: "1.5rem", fontFamily: "sans-serif" }}>
      <h1>Admin / Dispatcher</h1>
      <div style={{ display: "flex", gap: "0.5rem", marginBottom: "1rem" }}>
        <input
          placeholder="Username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
        />
        <input
          placeholder="Password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <button type="button" onClick={saveCredentials}>
          Save
        </button>
      </div>
      {message ? <p>{message}</p> : null}

      <section style={{ marginBottom: "1.5rem" }}>
        <h2>Leads</h2>
        <label>
          Status filter:
          <input
            value={leadStatusFilter}
            onChange={(e) => setLeadStatusFilter(e.target.value.toUpperCase())}
            placeholder="e.g. CONTACTED"
          />
        </label>
        <ul>
          {leads.map((lead) => (
            <li key={lead.lead_id} style={{ marginBottom: "0.5rem" }}>
              <div>
                <strong>{lead.name}</strong> ({lead.email || "no email"}) – {lead.status}
              </div>
              <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
                {[
                  "CONTACTED",
                  "BOOKED",
                  "DONE",
                  "CANCELLED",
                ].map((status) => (
                  <button key={status} type="button" onClick={() => updateLeadStatus(lead.lead_id, status)}>
                    {status}
                  </button>
                ))}
              </div>
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h2>Bookings</h2>
        <label>
          Date:
          <input type="date" value={selectedDate} onChange={(e) => setSelectedDate(e.target.value)} />
        </label>
        <ul>
          {bookings.map((booking) => (
            <li key={booking.booking_id} style={{ marginBottom: "0.5rem" }}>
              <div>
                <strong>{booking.status}</strong> – {formatDateTime(booking.starts_at)} – {booking.duration_minutes}m
              </div>
              <div>{booking.lead_name || "Unassigned"}</div>
              <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
                <button type="button" onClick={() => performBookingAction(booking.booking_id, "confirm")}>
                  Confirm
                </button>
                <button type="button" onClick={() => performBookingAction(booking.booking_id, "cancel")}>
                  Cancel
                </button>
                <button type="button" onClick={() => rescheduleBooking(booking.booking_id)}>
                  Reschedule
                </button>
              </div>
            </li>
          ))}
        </ul>
        <h3>Week view</h3>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(7, 1fr)", gap: "0.5rem" }}>
          {weekView.map((day) => (
            <div key={day.date} style={{ border: "1px solid #ccc", padding: "0.5rem" }}>
              <strong>{day.label}</strong>
              <div>{day.items.length} bookings</div>
              <ul>
                {day.items.map((booking) => (
                  <li key={booking.booking_id}>{formatDateTime(booking.starts_at)}</li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
