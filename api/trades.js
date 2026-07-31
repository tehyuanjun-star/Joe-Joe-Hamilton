// Vercel proxy — /api/trades
// Browser → Vercel → Supabase（解决国内 DNS 无法解析 supabase.co 问题）
module.exports = async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_ANON_KEY;
  if (!url || !key) return res.status(503).json({ error: 'Supabase not configured' });

  const base = `${url}/rest/v1/trades`;
  const headers = {
    apikey: key,
    Authorization: `Bearer ${key}`,
    'Content-Type': 'application/json',
  };

  try {
    // GET — load all trades
    if (req.method === 'GET') {
      const r = await fetch(`${base}?select=id,data&order=updated_at.desc`, { headers });
      const data = await r.json();
      return res.status(r.status).json(r.ok ? { data } : { error: data });
    }

    // POST — upsert one or multiple trades
    if (req.method === 'POST') {
      const body = req.body;
      const rows = Array.isArray(body) ? body : [body];
      const r = await fetch(`${base}?on_conflict=id`, {
        method: 'POST',
        headers: { ...headers, Prefer: 'resolution=merge-duplicates,return=minimal' },
        body: JSON.stringify(rows),
      });
      const text = await r.text();
      return res.status(r.status).json(r.ok ? { ok: true } : { error: text });
    }

    // DELETE — delete by id
    if (req.method === 'DELETE') {
      const id = req.query.id;
      if (!id) return res.status(400).json({ error: 'id required' });
      const r = await fetch(`${base}?id=eq.${encodeURIComponent(id)}`, {
        method: 'DELETE',
        headers,
      });
      return res.status(r.status).json({ ok: r.ok });
    }

    return res.status(405).json({ error: 'Method not allowed' });
  } catch (e) {
    return res.status(500).json({ error: e.message });
  }
};
