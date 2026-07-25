import { APP_VERSION, MAX_BULK_COUNT, EXPIRES_HOURS, RATE_LIMIT_WINDOW, RATE_LIMIT_MAX } from '../config.js';
import { generateUsername, jsonResponse, errorResponse, getClientIP } from '../utils.js';

// ─── Rate Limiter (in-memory, per-worker-instance) ───────────────────────────

const rateLimitMap = new Map();

function checkRateLimit(ip) {
  const now = Date.now();
  const windowMs = RATE_LIMIT_WINDOW * 1000;

  // Lazy cleanup when map gets large
  cleanupRateLimits();

  if (!rateLimitMap.has(ip)) {
    rateLimitMap.set(ip, { count: 1, windowStart: now });
    return true;
  }

  const entry = rateLimitMap.get(ip);

  // Reset window if expired
  if (now - entry.windowStart > windowMs) {
    rateLimitMap.set(ip, { count: 1, windowStart: now });
    return true;
  }

  // Check limit
  if (entry.count >= RATE_LIMIT_MAX) {
    return false;
  }

  entry.count++;
  return true;
}

// Periodically clean up stale entries (lazy cleanup on each check)
function cleanupRateLimits() {
  const now = Date.now();
  const windowMs = RATE_LIMIT_WINDOW * 1000;
  if (rateLimitMap.size > 1000) {
    for (const [ip, entry] of rateLimitMap) {
      if (now - entry.windowStart > windowMs) {
        rateLimitMap.delete(ip);
      }
    }
  }
}

// ─── Route Handlers ──────────────────────────────────────────────────────────

async function handleHealth(env, domain) {
  let dbConnected = false;
  let mailboxCount = 0;
  let messageCount = 0;

  try {
    const mb = await env.DB.prepare('SELECT COUNT(*) as count FROM mailboxes').all();
    mailboxCount = mb.results[0]?.count || 0;

    const msg = await env.DB.prepare('SELECT COUNT(*) as count FROM messages').all();
    messageCount = msg.results[0]?.count || 0;

    dbConnected = true;
  } catch (e) {
    console.error('DB health check failed:', e.message);
  }

  return jsonResponse({
    code: 0,
    data: {
      status: dbConnected ? 'healthy' : 'degraded',
      version: APP_VERSION,
      domain,
      db_connected: dbConnected,
      stats: { mailboxes: mailboxCount, messages: messageCount },
      endpoints: [
        'POST   /api/generate',
        'POST   /api/bulk-generate',
        'GET    /api/inbox/:address',
        'GET    /api/message/:id',
        'DELETE /api/mailbox/:address',
        'GET    /api/domains',
        'GET    /api/health',
      ],
    },
  });
}

async function handleGenerate(env, domain) {
  const username = generateUsername(10);
  const address = `${username}@${domain}`;

  await env.DB.prepare(
    `INSERT OR IGNORE INTO mailboxes (address, expires_at)
     VALUES (?, datetime('now', '+${EXPIRES_HOURS} hours'))`
  ).bind(address).run();

  return jsonResponse({
    code: 0,
    data: {
      address,
      mailId: username,
      endAt: new Date(Date.now() + EXPIRES_HOURS * 3600000).toISOString(),
    },
  });
}

async function handleBulkGenerate(request, env, domain) {
  const body = await request.json().catch(() => ({}));
  const count = Math.min(body.count || 1, MAX_BULK_COUNT);
  const emails = [];

  for (let i = 0; i < count; i++) {
    const username = generateUsername(10);
    const address = `${username}@${domain}`;
    emails.push({
      address,
      mailId: username,
      endAt: new Date(Date.now() + EXPIRES_HOURS * 3600000).toISOString(),
    });

    await env.DB.prepare(
      `INSERT OR IGNORE INTO mailboxes (address, expires_at)
       VALUES (?, datetime('now', '+${EXPIRES_HOURS} hours'))`
    ).bind(address).run();
  }

  return jsonResponse({ code: 0, data: emails });
}

async function handleInbox(path, env) {
  const address = decodeURIComponent(path.replace('/api/inbox/', '')).toLowerCase();

  await env.DB.prepare(
    'INSERT OR IGNORE INTO mailboxes (address) VALUES (?)'
  ).bind(address).run();

  const { results } = await env.DB.prepare(
    `SELECT id, sender, subject, received_at
     FROM messages
     WHERE mailbox_address = ?
     ORDER BY received_at DESC
     LIMIT 50`
  ).bind(address).all();

  return jsonResponse({
    code: 0,
    data: {
      rows: results.map((r) => ({
        id: r.id,
        from: r.sender,
        subject: r.subject,
        date: r.received_at,
      })),
    },
  });
}

async function handleMessage(path, env) {
  const id = path.replace('/api/message/', '');

  const { results } = await env.DB.prepare(
    'SELECT * FROM messages WHERE id = ?'
  ).bind(id).all();

  if (!results.length) {
    return errorResponse('Message not found', `ID: ${id}`, 404);
  }

  const msg = results[0];
  return jsonResponse({
    code: 0,
    data: {
      id: msg.id,
      from: msg.sender,
      subject: msg.subject,
      text: msg.text_body,
      html: msg.html_body,
      headers: msg.raw_headers,
      date: msg.received_at,
    },
  });
}

async function handleDeleteMailbox(path, env) {
  const address = decodeURIComponent(path.replace('/api/mailbox/', '')).toLowerCase();

  await env.DB.prepare('DELETE FROM messages WHERE mailbox_address = ?').bind(address).run();
  await env.DB.prepare('DELETE FROM mailboxes WHERE address = ?').bind(address).run();

  return jsonResponse({ code: 0, data: { message: 'Mailbox deleted', address } });
}

// ─── Router ──────────────────────────────────────────────────────────────────

export async function handleApi(request, env) {
  const url = new URL(request.url);
  const path = url.pathname;
  const method = request.method;
  const domain = env.DOMAIN;

  // Rate limit write operations
  if (method === 'POST') {
    const ip = getClientIP(request);
    if (!checkRateLimit(ip)) {
      return errorResponse(
        'Rate limit exceeded',
        `Max ${RATE_LIMIT_MAX} requests per hour`,
        429
      );
    }
  }

  // Routes
  if (path === '/' && method === 'GET') return handleHealth(env, domain);
  if (path === '/api/health' && method === 'GET') return handleHealth(env, domain);
  if (path === '/api/generate' && method === 'POST') return handleGenerate(env, domain);
  if (path === '/api/bulk-generate' && method === 'POST') return handleBulkGenerate(request, env, domain);
  if (path.startsWith('/api/inbox/') && method === 'GET') return handleInbox(path, env);
  if (path.startsWith('/api/message/') && method === 'GET') return handleMessage(path, env);
  if (path.startsWith('/api/mailbox/') && method === 'DELETE') return handleDeleteMailbox(path, env);
  if (path === '/api/domains' && method === 'GET') return jsonResponse({ code: 0, data: [domain] });

  return errorResponse('Not found', `No route matches ${method} ${path}`, 404);
}
