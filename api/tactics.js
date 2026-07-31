// Vercel proxy — /api/tactics
module.exports = async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_ANON_KEY;
  if (!url || !key) return res.status(503).json({ error: 'Supabase not configured' });

  const base = `${url}/rest/v1/tactics`;
  const headers = {
    apikey: key,
    Authorization: `Bearer ${key}`,
    'Content-Type': 'application/json',
  };

  try {
    if (req.method === 'GET') {
      const r = await fetch(`${base}?select=items&user_id=eq.default&limit=1`, { headers });
      const data = await r.json();
      return res.status(r.status).json(r.ok ? { data } : { error: data });
    }

    if (req.method === 'POST') {
      const r = await fetch(`${base}?on_conflict=user_id`, {
        method: 'POST',
        headers: { ...headers, Prefer: 'resolution=merge-duplicates,return=minimal' },
        body: JSON.stringify([req.body]),
      });
      const text = await r.text();
      return res.status(r.status).json(r.ok ? { ok: true } : { error: text });
    }

    return res.status(405).json({ error: 'Method not allowed' });
  } catch (e) {
    return res.status(500).json({ error: e.message });
  }
};
