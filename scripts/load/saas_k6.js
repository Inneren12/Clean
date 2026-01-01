import http from 'k6/http';
import { check, group, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const BASE_URL = __ENV.LOAD_TARGET_HOST || 'http://localhost:8000';
const AUTH_TOKEN = __ENV.LOAD_BEARER_TOKEN || '';
const ADMIN_BASIC = __ENV.LOAD_ADMIN_BASIC || '';
const DISPATCH_BASIC = __ENV.LOAD_DISPATCH_BASIC || ADMIN_BASIC;
const STORAGE_PUT_URL = __ENV.LOAD_STORAGE_PUT_URL || '';
const STORAGE_GET_URL = __ENV.LOAD_STORAGE_GET_URL || '';
const STORAGE_BYTES = Number(__ENV.LOAD_STORAGE_BYTES || '4096');
const METRICS_ENDPOINT = __ENV.LOAD_METRICS_URL || '';

export const options = {
  vus: Number(__ENV.LOAD_VUS || '5'),
  duration: __ENV.LOAD_DURATION || '1m',
  thresholds: {
    http_req_failed: ['rate<0.05'],
    'http_req_duration{group:lead_flow}': ['p(95)<1000', 'p(99)<2000'],
    'http_req_duration{group:admin_lists}': ['p(95)<1000', 'p(99)<2000'],
  },
};

const errorRate = new Rate('errors');
const storageUploadLatency = new Trend('storage_upload_latency_ms');
const storageDownloadLatency = new Trend('storage_download_latency_ms');
const dbConnections = new Trend('db_connections');

function authHeaders(extra = {}) {
  const headers = { ...extra };
  if (AUTH_TOKEN) {
    headers.Authorization = `Bearer ${AUTH_TOKEN}`;
  }
  return headers;
}

function adminHeaders() {
  if (!ADMIN_BASIC) return {};
  return { Authorization: `Basic ${ADMIN_BASIC}` };
}

function dispatchHeaders() {
  if (!DISPATCH_BASIC) return adminHeaders();
  return { Authorization: `Basic ${DISPATCH_BASIC}` };
}

function leadPayload() {
  const now = new Date();
  const isoDate = now.toISOString();
  return {
    name: `Load Test ${isoDate}`,
    phone: '780-555-1234',
    email: `lt-${now.getTime()}@example.com`,
    postal_code: 'T5J0N3',
    preferred_dates: ['Sat afternoon'],
    access_notes: 'Intercom #1203',
    structured_inputs: {
      beds: 2,
      baths: 1.5,
      cleaning_type: 'deep',
      heavy_grease: false,
      multi_floor: false,
      frequency: 'one_time',
      add_ons: {
        oven: true,
        fridge: false,
        microwave: true,
        cabinets: false,
        windows_up_to_5: false,
        balcony: false,
        linen_beds: 0,
        steam_armchair: 0,
        steam_sofa_2: 0,
        steam_sofa_3: 0,
        steam_sectional: 0,
        steam_mattress: 0,
        carpet_spot: 0,
      },
    },
    estimate_snapshot: {
      pricing_config_id: 'economy',
      pricing_config_version: 'v1',
      config_hash: 'load-test',
      rate: 35.0,
      team_size: 2,
      time_on_site_hours: 3.5,
      billed_cleaner_hours: 7.0,
      labor_cost: 245.0,
      discount_amount: 0.0,
      add_ons_cost: 50.0,
      total_before_tax: 295.0,
      assumptions: [],
      missing_info: [],
      confidence: 1.0,
    },
  };
}

function createLead() {
  const res = http.post(`${BASE_URL}/v1/leads`, JSON.stringify(leadPayload()), {
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    tags: { group: 'lead_flow', step: 'lead' },
  });
  const ok = check(res, {
    'lead created': (r) => r.status === 200 || r.status === 201,
  });
  if (!ok) {
    errorRate.add(1);
    return null;
  }
  try {
    return res.json('lead_id');
  } catch (err) {
    errorRate.add(1);
    return null;
  }
}

function pickSlot(durationHours) {
  const date = new Date();
  date.setDate(date.getDate() + 2);
  const params = {
    date: date.toISOString().slice(0, 10),
    time_on_site_hours: durationHours,
    postal_code: 'T5J0N3',
  };
  const res = http.get(`${BASE_URL}/v1/slots`, { params, tags: { group: 'lead_flow', step: 'slots' } });
  if (!check(res, { 'slots ok': (r) => r.status === 200 })) {
    errorRate.add(1);
    return null;
  }
  const body = res.json();
  if (!body || !body.slots || !body.slots.length) {
    return null;
  }
  return body.slots[0];
}

function createBooking(leadId, slotIso) {
  const payload = {
    starts_at: slotIso,
    time_on_site_hours: 3.5,
    lead_id: leadId,
    service_type: 'deep',
  };
  const res = http.post(`${BASE_URL}/v1/bookings`, JSON.stringify(payload), {
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    tags: { group: 'lead_flow', step: 'booking' },
  });
  const ok = check(res, { 'booking created': (r) => r.status === 201 });
  if (!ok) {
    errorRate.add(1);
    return null;
  }
  try {
    return res.json('booking_id');
  } catch (err) {
    errorRate.add(1);
    return null;
  }
}

function adminListEndpoints() {
  const headers = { ...adminHeaders() };
  if (!headers.Authorization) {
    return;
  }
  const listRes = http.get(`${BASE_URL}/v1/admin/leads`, { headers, tags: { group: 'admin_lists', step: 'leads' } });
  check(listRes, { 'admin leads ok': (r) => r.status === 200 || r.status === 401 || r.status === 403 });
  const bookings = http.get(`${BASE_URL}/v1/admin/bookings`, { headers, tags: { group: 'admin_lists', step: 'bookings' } });
  check(bookings, { 'admin bookings ok': (r) => r.status === 200 || r.status === 401 || r.status === 403 });
}

function triggerEmailJobs() {
  const headers = { 'Content-Type': 'application/json', ...dispatchHeaders() };
  if (!headers.Authorization) return;
  const res = http.post(`${BASE_URL}/v1/admin/email-scan`, null, { headers, tags: { group: 'lead_flow', step: 'email' } });
  check(res, { 'email scan accepted': (r) => r.status === 202 || r.status === 401 || r.status === 403 });
}

function webhookPayment(invoiceId) {
  const secretProvided = !!__ENV.LOAD_STRIPE_SIGNATURE && !!__ENV.LOAD_STRIPE_EVENT_BODY;
  if (!secretProvided) return;
  const headers = {
    'Stripe-Signature': __ENV.LOAD_STRIPE_SIGNATURE,
    'Content-Type': 'application/json',
  };
  const body = __ENV.LOAD_STRIPE_EVENT_BODY.replace('{INVOICE_ID}', invoiceId || 'test');
  const res = http.post(`${BASE_URL}/v1/payments/stripe/webhook`, body, { headers, tags: { group: 'lead_flow', step: 'webhook' } });
  check(res, { 'webhook processed': (r) => r.status === 200 || r.status === 400 || r.status === 503 });
}

function storageRoundTrip() {
  if (!STORAGE_PUT_URL || !STORAGE_GET_URL) return;
  const payload = new Uint8Array(STORAGE_BYTES);
  const putRes = http.put(STORAGE_PUT_URL, payload, { tags: { group: 'storage', step: 'upload' } });
  storageUploadLatency.add(putRes.timings.duration);
  check(putRes, { 'storage upload ok': (r) => r.status >= 200 && r.status < 400 });
  const getRes = http.get(STORAGE_GET_URL, { tags: { group: 'storage', step: 'download' } });
  storageDownloadLatency.add(getRes.timings.duration);
  check(getRes, { 'storage download ok': (r) => r.status >= 200 && r.status < 400 });
}

function collectDbPoolMetric() {
  if (!METRICS_ENDPOINT) return;
  const res = http.get(METRICS_ENDPOINT, { tags: { group: 'metrics', step: 'db_pool' } });
  if (res.status !== 200) return;
  const lines = res.body.split('\n');
  const poolLine = lines.find((l) => l.startsWith('db_pool_connections_in_use'));
  if (!poolLine) return;
  const parts = poolLine.trim().split(' ');
  if (parts.length === 2) {
    const value = Number(parts[1]);
    if (!Number.isNaN(value)) {
      dbConnections.add(value);
    }
  }
}

export default function () {
  group('lead_flow', () => {
    const leadId = createLead();
    const slotIso = pickSlot(3.5);
    const bookingId = slotIso ? createBooking(leadId, slotIso) : null;
    if (bookingId) {
      triggerEmailJobs();
      webhookPayment(bookingId);
    }
  });

  group('admin_lists', () => {
    adminListEndpoints();
  });

  group('storage', () => {
    storageRoundTrip();
  });

  group('metrics', () => {
    collectDbPoolMetric();
  });

  sleep(Number(__ENV.LOAD_SLEEP || '1'));
}

export function handleSummary(data) {
  const groups = Object.entries(data.metrics)
    .filter(([name]) => name.startsWith('http_req_duration'))
    .map(([name, metric]) => ({ name, p95: metric.values['p(95)'], p99: metric.values['p(99)'] }));
  console.log('Latency percentiles per group:', JSON.stringify(groups, null, 2));
  return {};
}
