// Vercel Serverless Function — 返回 Supabase 配置
// 环境变量在 Vercel Dashboard → Settings → Environment Variables 中设置
// 本地开发：在项目根目录创建 .env.local 文件

export default function handler(req, res) {
  // 允许 CORS（本地开发时可能需要）
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET');

  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_ANON_KEY;

  if (!url || !key) {
    return res.status(503).json({ error: 'Supabase environment variables not configured' });
  }

  // 仅返回公开的 anon key（不暴露 service_role key）
  return res.status(200).json({ url, key });
}
