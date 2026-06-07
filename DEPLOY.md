# 交易日记 — Vercel + Supabase 部署指南

## 前置条件

- GitHub 账号（免费）
- Vercel 账号（免费，用 GitHub 登录）：https://vercel.com
- Supabase 账号（免费）：https://supabase.com

---

## 第一步：创建 Supabase 数据库

1. 登录 [Supabase](https://supabase.com) → **New Project**
2. 填写项目名称（如 `trading-journal`）、设置数据库密码、选择区域（推荐 `ap-northeast-1` 东京 或 `us-west-1`）
3. 等待约 1 分钟，项目创建完成
4. 进入项目 → 左侧菜单 **SQL Editor** → 点击 **New query**
5. 将 `supabase_schema.sql` 文件的全部内容粘贴进去 → 点击 **Run**
6. 看到 `trades | tactics | signals` 三行说明建表成功
7. 记录以下两个值（在 **Project Settings → API** 中）：
   - **Project URL**：`https://xxxxxxxxxxxx.supabase.co`
   - **anon public** Key：`eyJ...`（这是公开的只读密钥，可以放在前端）

---

## 第二步：把代码推送到 GitHub

```bash
# 在项目目录下（C:\Users\tehyu\Documents\Claude\Projects\交易日记网页）执行

git init
git add index.html api/ vercel.json .gitignore .env.example upload_signals.py supabase_schema.sql
git commit -m "initial commit: trading journal with Supabase sync"

# 在 GitHub 上创建一个 private 仓库（如 trading-journal）
# 然后推送：
git remote add origin https://github.com/你的用户名/trading-journal.git
git push -u origin main
```

> ⚠️ **绝不要** `git add .env.local`！`.gitignore` 已排除它，但请再次确认。

---

## 第三步：在 Vercel 部署

1. 登录 [Vercel](https://vercel.com) → **Add New Project**
2. 选择 **Import Git Repository** → 找到你刚推送的 `trading-journal` 仓库
3. 框架预设选 **Other**（不需要选 Next.js）
4. 点击 **Deploy**（第一次会成功，但还没配置环境变量，Supabase 功能暂时不工作）

---

## 第四步：配置 Supabase 环境变量

1. 在 Vercel 项目页面 → **Settings → Environment Variables**
2. 添加以下两个变量：

   | Name | Value |
   |------|-------|
   | `SUPABASE_URL` | `https://xxxxxxxxxxxx.supabase.co` |
   | `SUPABASE_ANON_KEY` | `eyJ...`（你的 anon public key） |

3. Environment 选 **Production + Preview + Development**（全选）
4. 点击 **Save**
5. 回到 **Deployments** 标签 → 找到最新部署 → 点右侧三点菜单 → **Redeploy**

---

## 第五步：访问并验证

1. 打开 Vercel 给你的域名（如 `https://trading-journal-xxxx.vercel.app`）
2. 右上角应显示 🟢 **云端同步**（绿色），表示已连接 Supabase
3. 新建一条测试交易，保存后：
   - 回到 Supabase → **Table Editor → trades** 表，应能看到新记录
4. 清空浏览器 localStorage（开发者工具 → Application → Local Storage → Clear）
5. 刷新页面，数据应从云端重新加载 ✓

---

## 本地开发（可选）

如果想在本地也使用 Supabase 同步：

1. 在项目根目录创建 `.env.local`（**不要提交到 git**）：

```
SUPABASE_URL=https://xxxxxxxxxxxx.supabase.co
SUPABASE_ANON_KEY=eyJ...
```

2. 安装 Vercel CLI：`npm i -g vercel`
3. 运行：`vercel dev`（会自动读取 `.env.local`）
4. 访问 `http://localhost:3000`

> 直接双击打开 `index.html`（`file://` 协议）时，`/api/config` 请求失败，
> 自动降级为 localStorage 本地模式，所有功能照常可用。

---

## 第六步：自定义域名（可选）

1. Vercel → Settings → Domains → 添加你的域名
2. 按提示在域名注册商处添加 DNS 记录
3. 几分钟后生效，HTTPS 自动配置

---

## 第七步：上传扫描信号（可选）

scanner.py 运行后会生成 `scan_report_YYYY-MM-DD.json`，用以下命令上传到 Supabase：

```bash
# 确保 .env.local 已配置，或直接设置环境变量
python upload_signals.py                          # 上传最新报告
python upload_signals.py scan_report_2026-06-07.json  # 指定文件
```

---

## 数据安全说明

| 密钥 | 位置 | 是否公开 |
|------|------|----------|
| `SUPABASE_URL` | Vercel 环境变量 | ⚠️ 可见（但无法单独用于写入） |
| `SUPABASE_ANON_KEY` | Vercel 环境变量 | ⚠️ 通过 `/api/config` 返回给前端（这是设计如此） |
| `SUPABASE_SERVICE_ROLE_KEY` | **从不使用** | — |
| 数据库密码 | 仅 Supabase Dashboard | 🔒 不对外暴露 |

> `anon key` 是 Supabase 设计的公开密钥，通过 RLS 策略控制权限。
> 即使别人拿到，RLS 策略会阻止未授权访问。这是 Supabase 官方推荐的前端集成方式。

---

## 常见问题

**Q: 右上角一直显示 ⚫ 本地，不是绿色？**
A: 检查 Vercel 环境变量是否正确配置，且已重新部署。打开浏览器开发者工具 Console 查看错误信息。

**Q: Vercel 部署失败？**
A: 查看 Vercel 部署日志。常见原因：`api/config.js` 使用了 ES Module 语法（`export default`），需要 Node.js 18+。Vercel 默认支持，通常不需要额外配置。

**Q: 数据在 Supabase 里但刷新后前端看不到？**
A: 检查 Supabase RLS 策略是否正确执行了 `supabase_schema.sql`。

**Q: 本地 `python upload_signals.py` 报环境变量错误？**
A: 创建 `.env.local` 文件，或在命令行中手动设置：
```bash
set SUPABASE_URL=https://...
set SUPABASE_ANON_KEY=eyJ...
python upload_signals.py
```
