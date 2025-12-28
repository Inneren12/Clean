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

type ExportEvent = {
  event_id: string;
  lead_id?: string | null;
  mode: string;
  target_url_host?: string | null;
  attempts: number;
  last_error_code?: string | null;
  created_at: string;
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

function statusBadge(status?: string) {
  const normalized = (status ?? "").toLowerCase();
  const className = `status-badge ${normalized}`;
  return <span className={className}>{status || "UNKNOWN"}</span>;
}

export default function AdminPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [leads, setLeads] = useState<Lead[]>([]);
  const [bookings, setBookings] = useState<Booking[]>([]);
  const [exportEvents, setExportEvents] = useState<ExportEvent[]>([]);
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

  const loadExportDeadLetter = async () => {
    if (!username || !password) return;
    const response = await fetch(`${API_BASE}/v1/admin/export-dead-letter?limit=50`, {
      headers: authHeaders,
      cache: "no-store",
    });
    if (!response.ok) return;
    const data = (await response.json()) as ExportEvent[];
    setExportEvents(data);
  };

  useEffect(() => {
    void loadLeads();
    void loadExportDeadLetter();
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
    <div className="admin-page">
      <div className="admin-section">
        <h1>Admin / Dispatcher</h1>
        <p className="muted">Save credentials locally, then load leads, bookings, and exports.</p>
      </div>

      <div className="admin-card">
        <div className="admin-section">
          <h2>Credentials</h2>
          <div className="admin-actions">
            <input placeholder="Username" value={username} onChange={(e) => setUsername(e.target.value)} />
            <input
              placeholder="Password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <button className="btn btn-primary" type="button" onClick={saveCredentials}>
              Save
            </button>
            <button className="btn btn-ghost" type="button" onClick={clearCredentials}>
              Clear
            </button>
          </div>
          {message ? <p className="alert alert-success">{message}</p> : null}
        </div>
      </div>

      <div className="admin-grid">
        <section className="admin-card admin-section">
          <div className="section-heading">
            <h2>Leads</h2>
            <p className="muted">Filter and set statuses directly.</p>
          </div>
          <div className="admin-actions">
            <label style={{ width: "100%" }}>
              <span className="label">Status filter</span>
              <input
                value={leadStatusFilter}
                onChange={(e) => setLeadStatusFilter(e.target.value.toUpperCase())}
                placeholder="e.g. CONTACTED"
              />
            </label>
            <button className="btn btn-ghost" type="button" onClick={() => void loadLeads()}>
              Refresh
            </button>
          </div>
          <table className="table-like">
            <thead>
              <tr>
                <th>Name</th>
                <th>Email</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {leads.map((lead) => (
                <tr key={lead.lead_id}>
                  <td>{lead.name}</td>
                  <td>{lead.email || "no email"}</td>
                  <td>{statusBadge(lead.status)}</td>
                  <td>
                    <div className="admin-actions">
                      {["CONTACTED", "BOOKED", "DONE", "CANCELLED"].map((status) => (
                        <button key={status} className="btn btn-ghost" type="button" onClick={() => updateLeadStatus(lead.lead_id, status)}>
                          {status}
                        </button>
                      ))}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <section className="admin-card admin-section">
          <div className="section-heading">
            <h2>Export dead-letter</h2>
            <p className="muted">Failed webhook deliveries (latest 50).</p>
          </div>
          <div className="admin-actions">
            <button className="btn btn-ghost" type="button" onClick={() => void loadExportDeadLetter()}>
              Refresh
            </button>
          </div>
          {exportEvents.length === 0 ? <div className="muted">No failed exports recorded.</div> : null}
          <div className="dead-letter-list">
            {exportEvents.map((event) => (
              <div key={event.event_id} className="admin-card">
                <div className="admin-section">
                  <div className="admin-actions" style={{ justifyContent: "space-between" }}>
                    <strong>{event.mode}</strong>
                    <span className="muted">{event.target_url_host ?? "unknown host"}</span>
                  </div>
                  <div className="muted">
                    Attempts: {event.attempts} · Lead: {event.lead_id ?? "unknown"} · Created: {formatDateTime(event.created_at)}
                  </div>
                  <div className="muted">Last error: {event.last_error_code || "unknown"}</div>
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>

      <section className="admin-card admin-section">
        <div className="section-heading">
          <h2>Bookings</h2>
          <p className="muted">Day view with actions, plus a quick week glance.</p>
        </div>
        <div className="admin-actions">
          <label>
            <span className="label">Date</span>
            <input type="date" value={selectedDate} onChange={(e) => setSelectedDate(e.target.value)} />
          </label>
          <button className="btn btn-ghost" type="button" onClick={() => void loadBookings()}>
            Refresh
          </button>
        </div>
        <table className="table-like">
          <thead>
            <tr>
              <th>When</th>
              <th>Status</th>
              <th>Lead</th>
              <th>Duration</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {bookings
              .filter((booking) => bookingLocalYMD(booking.starts_at) === selectedDate)
              .map((booking) => (
                <tr key={booking.booking_id}>
                  <td>{formatDateTime(booking.starts_at)}</td>
                  <td>{statusBadge(booking.status)}</td>
                  <td>
                    <div>{booking.lead_name || "Unassigned"}</div>
                    <div className="muted">{booking.lead_email || "no email"}</div>
                  </td>
                  <td>{booking.duration_minutes}m</td>
                  <td>
                    <div className="admin-actions">
                      <button className="btn btn-ghost" type="button" onClick={() => performBookingAction(booking.booking_id, "confirm")}>
                        Confirm
                      </button>
                      <button className="btn btn-ghost" type="button" onClick={() => performBookingAction(booking.booking_id, "cancel")}>
                        Cancel
                      </button>
                      <button className="btn btn-secondary" type="button" onClick={() => rescheduleBooking(booking.booking_id)}>
                        Reschedule
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
          </tbody>
        </table>

        <h3>Week view</h3>
        <div className="slot-grid">
          {weekView.map((day) => (
            <div key={day.date} className="slot-column admin-card" style={{ boxShadow: "none" }}>
              <div className="admin-section">
                <strong>{day.label}</strong>
                <div className="muted">{day.items.length} bookings</div>
                <ul style={{ paddingLeft: 16, margin: 0, display: "grid", gap: 6 }}>
                  {day.items.map((booking) => (
                    <li key={booking.booking_id}>• {formatDateTime(booking.starts_at)}</li>
                  ))}
                </ul>
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
