-- ══════════════════════════════════════════════════
-- 交易日记 Supabase 数据库结构
-- 在 Supabase Dashboard → SQL Editor 中执行
-- ══════════════════════════════════════════════════

-- ── 1. 交易记录表 ────────────────────────────────
CREATE TABLE IF NOT EXISTS trades (
  id          TEXT        PRIMARY KEY,          -- 前端生成的 timestamp ID
  data        JSONB       NOT NULL,             -- 完整交易对象（含图表 base64）
  user_id     TEXT        NOT NULL DEFAULT 'default',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 加速按更新时间排序查询
CREATE INDEX IF NOT EXISTS trades_updated_at_idx ON trades (updated_at DESC);
CREATE INDEX IF NOT EXISTS trades_user_id_idx    ON trades (user_id);

-- ── 2. 出场策略表 ────────────────────────────────
CREATE TABLE IF NOT EXISTS tactics (
  user_id     TEXT        PRIMARY KEY DEFAULT 'default',
  items       JSONB       NOT NULL DEFAULT '[]',
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── 3. 扫描信号表（scanner.py 生成，upload_signals.py 上传）────
CREATE TABLE IF NOT EXISTS signals (
  id          BIGSERIAL   PRIMARY KEY,
  scan_date   DATE        NOT NULL UNIQUE,      -- 每天只保留最新一份
  data        JSONB       NOT NULL,             -- 完整 scan_report JSON
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS signals_scan_date_idx ON signals (scan_date DESC);

-- ══════════════════════════════════════════════════
-- Row Level Security（RLS）策略
-- 这是单用户私人应用，允许 anon key 完全访问
-- 如果将来多用户，改为按 auth.uid() 过滤
-- ══════════════════════════════════════════════════

ALTER TABLE trades  ENABLE ROW LEVEL SECURITY;
ALTER TABLE tactics ENABLE ROW LEVEL SECURITY;
ALTER TABLE signals ENABLE ROW LEVEL SECURITY;

-- trades：允许 anon 读写（单用户）
DROP POLICY IF EXISTS "anon_all_trades"  ON trades;
CREATE POLICY "anon_all_trades"  ON trades
  FOR ALL TO anon
  USING (true)
  WITH CHECK (true);

-- tactics：允许 anon 读写
DROP POLICY IF EXISTS "anon_all_tactics" ON tactics;
CREATE POLICY "anon_all_tactics" ON tactics
  FOR ALL TO anon
  USING (true)
  WITH CHECK (true);

-- signals：允许 anon 读写
DROP POLICY IF EXISTS "anon_all_signals" ON signals;
CREATE POLICY "anon_all_signals" ON signals
  FOR ALL TO anon
  USING (true)
  WITH CHECK (true);

-- ══════════════════════════════════════════════════
-- 验证：执行后应看到三张表
-- ══════════════════════════════════════════════════
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('trades', 'tactics', 'signals');
