import PostalMime from 'postal-mime';

export async function handleEmail(message, env) {
  const to = message.to;
  const from = message.from;
  const subject = message.headers.get('subject') || '(no subject)';

  console.log(`📧 Email received: ${from} → ${to} | Subject: ${subject}`);

  const address = to.toLowerCase().trim();

  // Parse email body
  let textBody = '';
  let htmlBody = '';
  let rawHeaders = '';

  try {
    const rawEmail = await new Response(message.raw).arrayBuffer();
    const parser = new PostalMime();
    const parsed = await parser.parse(rawEmail);

    textBody = parsed.text || '';
    htmlBody = parsed.html || '';

    const headersObj = {};
    for (const [key, value] of message.headers) {
      headersObj[key] = value;
    }
    rawHeaders = JSON.stringify(headersObj);

    console.log(`📧 Parsed: text=${textBody.length} chars, html=${htmlBody.length} chars`);
  } catch (e) {
    console.error('Email parse error:', e.message);
    textBody = '(could not parse email body)';
    rawHeaders = JSON.stringify({ error: e.message });
  }

  // Ensure mailbox exists
  await env.DB.prepare(
    'INSERT OR IGNORE INTO mailboxes (address) VALUES (?)'
  ).bind(address).run();

  // Store message
  await env.DB.prepare(
    `INSERT INTO messages (mailbox_address, sender, subject, text_body, html_body, raw_headers)
     VALUES (?, ?, ?, ?, ?, ?)`
  ).bind(address, from, subject, textBody, htmlBody, rawHeaders).run();

  console.log(`✅ Stored message for ${address}`);
}
