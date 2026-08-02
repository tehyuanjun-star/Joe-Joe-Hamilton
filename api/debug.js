// 临时诊断端点 — 上线稳定后可删除
module.exports = async function handler(req, res) {
  const url = process.env.SUPABASE_URL || '';
  const key = process.env.SUPABASE_ANON_KEY || '';

  const info = {
    url_prefix:    url.slice(0, 40),          // 只显示前40字符
    url_length:    url.length,
    key_prefix:    key.slice(0, 20),
    key_length:    key.length,
    node_version:  process.version,
    fetch_exists:  typeof fetch !== 'undefined',
  };

  if (!url || !key) {
    return res.status(200).json({ ...info, status: '环境变量缺失' });
  }

  try {
    const testUrl = `${url}/rest/v1/signals?select=scan_date&limit=1`;
    info.test_url = testUrl.slice(0, 60);

    const r = await fetch(testUrl, {
      headers: {
        apikey: key,
        Authorization: `Bearer ${key}`,
      },
    });

    const body = await r.text();
    return res.status(200).json({
      ...info,
      http_status: r.status,
      http_ok:     r.ok,
      body_preview: body.slice(0, 200),
    });
  } catch (e) {
    return res.status(200).json({
      ...info,
      error_type:    e.constructor.name,
      error_message: e.message,
      error_code:    e.code || null,
    });
  }
};
