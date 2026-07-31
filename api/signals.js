// Vercel proxy — /api/signals
module.exports = async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_ANON_KEY;
  if (!url || !key) return res.status(503).json({ data: null, error: 'Supabase not configured' });

  try {
    const r = await fetch(
      `${url}/rest/v1/signals?select=*&order=scan_date.desc&limit=1`,
      { headers: { apikey: key, Authorization: `Bearer ${key}` } }
    );
    const arr = await r.json();
    if (!r.ok) return res.status(r.status).json({ data: null, error: arr });
    return res.status(200).json({ data: arr[0] || null, error: null });
  } catch (e) {
    return res.status(500).json({ data: null, error: e.message });
  }
};
