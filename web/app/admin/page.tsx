"use client";

import { useEffect, useMemo, useState } from "react";

const STORAGE_USERNAME_KEY = "admin_basic_username";
const STORAGE_PASSWORD_KEY = "admin_basic_password";
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

function formatYMDInTz(date: Date, timeZone: string) {
  const formatter = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  const parts = formatter.formatToParts(date);
  const lookup: Record<string, string> = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${lookup.year}-${lookup.month}-${lookup.day}`;
}

function ymdToDate(ymd: string) {
  const [year, month, day] = ymd.split("-").map((value) => parseInt(value, 10));
  return new Date(Date.UTC(year, month - 1, day, 12, 0, 0));
}

function addDaysYMD(day: string, delta: number) {
  const base = ymdToDate(day);
  base.setUTCDate(base.getUTCDate() + delta);
  return formatYMDInTz(base, EDMONTON_TZ);
}

function bookingLocalYMD(startsAt: string) {
  return formatYMDInTz(new Date(startsAt), EDMONTON_TZ);
}

export default function AdminPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [leads, setLeads] = useState<Lead[]>([]);
  const [bookings, setBookings] = useState<Booking[]>([]);
  const [leadStatusFilter, setLeadStatusFilter] = useState<string>("");
  const [selectedDate, setSelectedDate] = useState<string>(() => {
    const today = new Date();
    return formatYMDInTz(today, EDMONTON_TZ);
  });
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const storedUsername = window.localStorage.getItem(STORAGE_USERNAME_KEY);
    const storedPassword = window.localStorage.getItem(STORAGE_PASSWORD_KEY);
    if (storedUsername) setUsername(storedUsername);
    if (storedPassword) setPassword(storedPassword);
  }, []);

  const authHeaders = useMemo<Record<string, string>>(() => {
    if (!username || !password) return {} as Record<string, string>;
    const encoded = btoa(`${username}:${password}`);
    return { Authorization: `Basic ${encoded}` };
  }, [username, password]);

  const loadLeads = async () => {
    if (!username || !password) return;
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
    if (!username || !password) return;
    const endDate = addDaysYMD(selectedDate, 6);
    const response = await fetch(
      `${API_BASE}/v1/admin/bookings?from=${selectedDate}&to=${endDate}`,
      { headers: authHeaders, cache: "no-store" }
    );
    if (!response.ok) return;
    const data = (await response.json()) as Booking[];
    setBookings(data);
  };

  useEffect(() => {
    void loadLeads();
  }, [authHeaders, leadStatusFilter]);

  useEffect(() => {
    void loadBookings();
  }, [authHeaders, selectedDate]);

  const saveCredentials = () => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem(STORAGE_USERNAME_KEY, username);
      window.localStorage.setItem(STORAGE_PASSWORD_KEY, password);
    }
    setMessage("Saved credentials");
  };

  const clearCredentials = () => {
    setUsername("");
    setPassword("");
    setBookings([]);
    setLeads([]);
    if (typeof window !== "undefined") {
      window.localStorage.removeItem(STORAGE_USERNAME_KEY);
      window.localStorage.removeItem(STORAGE_PASSWORD_KEY);
    }
    setMessage("Cleared credentials");
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
    const start = ymdToDate(selectedDate);
    const formatter = new Intl.DateTimeFormat("en-CA", {
      timeZone: EDMONTON_TZ,
      weekday: "short",
      month: "short",
      day: "numeric",
    });
    const days: { label: string; date: string; items: Booking[] }[] = [];
    for (let i = 0; i < 7; i++) {
      const d = new Date(start);
      d.setUTCDate(start.getUTCDate() + i);
      const key = formatYMDInTz(d, EDMONTON_TZ);
      days.push({
        label: formatter.format(d),
        date: key,
        items: bookings.filter((b) => bookingLocalYMD(b.starts_at) === key),
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
        <button type="button" onClick={clearCredentials}>
          Clear
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
          {bookings
            .filter((booking) => bookingLocalYMD(booking.starts_at) === selectedDate)
            .map((booking) => (
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
