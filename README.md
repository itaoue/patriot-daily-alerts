# Patriot Daily Alerts — 重构版

[patriotdailyalerts.com](https://patriotdailyalerts.com/) 的全新实现：把原来的 WordPress（MH Magazine 主题）站点重写为一个轻量的 **Flask + Postgres** 应用，代码托管在 **GitHub**，由 **Railway** 自动构建部署。

## 架构

```
GitHub (main 分支)  ──push──▶  Railway 自动构建 (Nixpacks)  ──▶  gunicorn + Flask
                                                                     │
                                          Railway Postgres  ◀────────┘  (DATABASE_URL 自动注入)
```

| 层 | 选型 | 说明 |
|---|---|---|
| Web | Flask 3 + Jinja2 | 服务端渲染，无前端构建步骤 |
| 数据 | SQLAlchemy 2 + Postgres（本地 SQLite） | 文章 / 分类 / 静态页 / 订阅者 / 留言 |
| 部署 | Railway Nixpacks + gunicorn | `railway.json` 定义启动命令与健康检查 |
| CI | GitHub Actions | ruff + pytest，每次 push / PR 自动跑 |
| 内容迁移 | `tools/import_wp.py` | 通过 WordPress REST API 一键导入全部 2,000+ 篇文章 |

## 功能

- **前台**：首页（头条 + Top stories + 最新网格 + Most read 侧栏）、突发新闻滚动条、分类页、全站搜索、文章页（阅读进度条、分享、相关文章、上一篇/下一篇）、静态页（About / Contact / Privacy / Terms / Report Spam）
- **URL 完全兼容旧站**：文章仍是 `/{slug}/`，分类仍是 `/category/{slug}/`，旧的 `/category/latest-news/` 301 到 `/latest/`，所以搜索引擎收录和外链不受影响
- **邮件订阅**：三处订阅入口（底部横幅、侧栏、文中）；蜜罐防机器人；退订页 `/remove-from-our-email-list/`；可选推送到 BigMailer
- **联系表单**：留言入库，后台可查看
- **SEO**：canonical、Open Graph、`NewsArticle` JSON-LD、`/feed/` RSS、`/sitemap.xml`、`/robots.txt`
- **后台 Newsroom** `/admin/`：密码登录、CSRF 保护、文章增删改（草稿 / 定时发布 / 置顶头条）、静态页编辑、订阅者导出 CSV、留言箱；正文 HTML 经 bleach 过滤
- 响应式布局、无障碍（跳转链接、aria、减少动画偏好）

## 本地运行

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # 按需修改 ADMIN_PASSWORD 等
FLASK_ENV=development ADMIN_PASSWORD=dev123 python src/main.py
```

打开 http://localhost:5000 。首次启动会自动建表，并用 `tools/seed_data.json`（旧站最新 40 篇文章 + 5 个静态页快照）填充数据库。

运行测试与检查：

```bash
ruff check src tools tests && pytest -q
```

## 部署到 Railway

1. 把本目录推到 GitHub 新仓库（例如 `patriot-daily-alerts`）。
2. Railway → **New Project → Deploy from GitHub repo**，选择该仓库。Railway 会读取 `railway.json` 自动用 Nixpacks 构建并以 gunicorn 启动。
3. 在同一个项目里 **Add → Database → PostgreSQL**。Railway 会自动把 `DATABASE_URL` 注入到 Web 服务。
4. 在 Web 服务的 **Variables** 里添加：

   | 变量 | 必填 | 说明 |
   |---|---|---|
   | `SECRET_KEY` | ✅ | 长随机串，用于会话签名 |
   | `ADMIN_PASSWORD` | ✅ | 后台登录密码 |
   | `SITE_URL` | ✅ | 如 `https://patriotdailyalerts.com`（用于 canonical / RSS / sitemap） |
   | `GA_MEASUREMENT_ID` | 可选 | Google Analytics 4 的 `G-XXXX` |
   | `BIGMAILER_API_KEY` / `BIGMAILER_BRAND_ID` / `BIGMAILER_LIST_ID` | 可选 | 填了就把新订阅者同步到 BigMailer |
   | `AUTO_SEED` | 可选 | 默认 `1`；库为空时自动灌入示例数据，正式导入后可设为 `0` |

5. **Settings → Networking** 生成域名，测试无误后绑定 `patriotdailyalerts.com` 自定义域名，并把 DNS 的 CNAME 指向 Railway。
6. 之后每次 `git push main`，GitHub Actions 跑测试，Railway 自动重新部署。

## 从旧 WordPress 站导入文章

**最简单的方式**：登录后台 → **Import** 页（`/admin/import`），填数量（默认最新 300 篇，0 = 全部）点 **Start import**。导入在 Railway 服务内部后台执行，页面自动刷新进度，不需要任何命令行或公网数据库地址。

也可以用命令行（约 5 分钟，可重复执行，按 WordPress ID 去重更新）：

```bash
# 本地（连接线上库）：
DATABASE_URL='postgresql://...railway...' python tools/import_wp.py

# 或在 Railway 的服务里执行一次性命令（Railway CLI）：
railway run python tools/import_wp.py
```

可加 `--max 300` 只导入最新 300 篇。导入完成后建议把 Railway 里的 `AUTO_SEED` 设为 `0`。

> 图片：导入的文章沿用旧站 `wp-content/uploads` 的图片 URL。旧站下线前，请把 `uploads` 目录同步到对象存储（如 Cloudflare R2 / S3），然后在数据库里批量替换域名即可。Railway 的文件系统是临时的，不适合直接存图片，所以后台的封面图字段采用 URL 而非上传。

## 目录结构

```
src/
  main.py            # 应用工厂、Jinja 过滤器、自动建表/灌数据
  config.py          # 环境变量 → 配置
  models.py          # Category / Post / Page / Subscriber / ContactMessage
  seed.py            # 首次启动的示例数据
  importer.py        # WordPress REST API 导入逻辑（后台按钮 + CLI 共用）
  utils.py           # slug、HTML 过滤、日期格式化
  routes/
    public.py        # 前台页面、RSS、sitemap、robots
    api.py           # 订阅 / 退订 / 联系表单 / 健康检查
    admin.py         # 后台 Newsroom
  templates/         # Jinja2 模板（partials/ 为公共片段，admin/ 为后台）
  static/            # site.css / admin.css / site.js / 图标
tools/
  import_wp.py       # 导入器的命令行入口
  seed_data.json     # 旧站快照
tests/test_app.py    # 端到端冒烟测试
Procfile / railway.json / runtime.txt / requirements.txt
.github/workflows/ci.yml
```
