import { corsHeaders } from './utils.js';
import { handleEmail } from './handlers/email.js';
import { handleApi } from './handlers/api.js';

export default {
  async email(message, env, ctx) {
    await handleEmail(message, env);
  },

  async fetch(request, env, ctx) {
    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: corsHeaders() });
    }
    return handleApi(request, env);
  },

  // Cron trigger: cleanup expired mailboxes (configure in wrangler.toml)
  async scheduled(event, env, ctx) {
    try {
      const now = new Date().toISOString();

      // Delete expired messages
      const expired = await env.DB.prepare(
        `SELECT address FROM mailboxes WHERE expires_at < ?`
      ).bind(now).all();

      let cleaned = 0;
      for (const mb of expired.results) {
        await env.DB.prepare(
          'DELETE FROM messages WHERE mailbox_address = ?'
        ).bind(mb.address).run();
        await env.DB.prepare(
          'DELETE FROM mailboxes WHERE address = ?'
        ).bind(mb.address).run();
        cleaned++;
      }

      console.log(`🧹 Cleanup: removed ${cleaned} expired mailboxes`);
    } catch (e) {
      console.error('Cleanup error:', e.message);
    }
  },
};
