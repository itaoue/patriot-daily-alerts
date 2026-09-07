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

## 图片：切域名前先镜像到仓库

文章里的图片地址都是 `https://patriotdailyalerts.com/wp-content/uploads/...` 的绝对路径。域名指向新站后这些地址会打到新站，所以切域名前要把文件搬过来：

```bash
python tools/migrate_images.py --max 300   # 与导入的文章数保持一致；0 = 全部（约 750 MB，不建议放 git）
git add src/static/uploads && git commit -m "Mirror images from WordPress" && git push
```

脚本把图片按原来的 `yyyy/mm/文件名` 存到 `src/static/uploads/`，Flask 在 `/wp-content/uploads/<路径>` 原样提供（一年缓存）。数据库里的地址不用改，切域名后自动生效，搜索引擎收录的旧图片地址也不会失效。重复运行只补缺失的文件。

Railway 的文件系统是临时的，所以后台的封面图字段采用 URL 而非上传；新文章的图片放到 `src/static/uploads/` 一起提交，或使用外部图床。

## 内容自动化流水线

每天两批（美东 6:00 三篇、14:00 两篇），由 GitHub Actions 的 `content.yml` 定时执行，全部以**草稿**进入后台，人工审核后再发布：

1. `tools/radar.py` 读取 Newsmax、Gateway Pundit、Western Journal、National Review、Washington Examiner、Fox、Daily Wire、Breitbart、NY Post、Daily Caller、Federalist、Just the News 的 RSS（被屏蔽的走 Google News），外加人物关注列表；把同一事件聚成一簇，按“几家同时报道 + 新鲜度 + 是否涉及关注人物”打分，并对照站内最近 14 天的文章去重。
2. `tools/write_stories.py` 对每个选题：Claude 用联网搜索和网页抓取读 2 到 3 家报道和一手来源 → 写 500 到 800 词原创稿（保守派视角、正文只用短引语并注明出处）→ 第二次 Claude 调用当编辑，逐条核对事实、引语和法律风险 → 配图（公众人物用 Wikimedia 授权照片，否则用 xAI 生成的无人脸新闻图，存入 `src/static/uploads/` 随代码提交）→ `POST /api/publish` 进入后台草稿，编辑意见显示在文章编辑页右侧。
3. 后台首页有 “Drafts awaiting review” 列表，把状态改为 Published 保存即可上线。

**成本控制**：每次调用都会在运行日志里打印 token 用量和估算费用，运行结束给出总额。默认 `STORY_EFFORT=medium`；调研每篇最多 3 次搜索加 3 次抓取、每页 6k token 并启用提示缓存。可在仓库 Variables 里设 `RESEARCH_MODEL` / `EDITOR_MODEL`（如 `claude-sonnet-5`）进一步降本，写稿仍用 `STORY_MODEL`。

GitHub 仓库需要配置 Secrets：`ANTHROPIC_API_KEY`、`XAI_API_KEY`、`PUBLISH_TOKEN`（与 Railway 变量 `PUBLISH_TOKEN` 相同的长随机串）。也可以在 Actions 页面手动触发 `Content pipeline`，指定篇数。文章署名默认 Joseph Sosa（旧站沿用的署名），要换的话在仓库 Settings → Variables 里加 `STORY_AUTHOR`。

本地试跑：

```bash
pip install -r requirements-content.txt
export ANTHROPIC_API_KEY=... XAI_API_KEY=... PUBLISH_TOKEN=... SITE_URL=https://patriotdailyalerts.com
python tools/radar.py --print          # 看选题
python tools/write_stories.py --dry-run   # 看会选哪几篇
python tools/write_stories.py --count 1   # 真写一篇进草稿
```

## 邮件 Newsletter

版式照搬 Middle America News 的早晚报：600px 单栏、logo 页头、深蓝的“edition + 日期”条、三条头条（大标题链接 + 全宽配图 + 红色 READ MORE 按钮）、Also Trending 标题列表、可选 SPONSORED 广告位、灰色页脚（退订、隐私、邮政地址、免责声明）。

- `tools/build_newsletter.py`：从站点接口取已发布文章，最近 16 小时内的前三篇做头条，其余做 Also Trending；主题 = 头条标题，预览文字 = “and 第二条标题”；所有链接带 UTM。输出到 `dist/newsletters/<日期>-<am|pm>.html/.txt/.json`，并复制一份到 `src/static/newsletters/` 作为 “View online” 页面。
- `tools/push_newsletter.py`：推到 BigMailer 建草稿活动（`--ready` 直接标记可发送）。需要 `BIGMAILER_API_KEY`、`BIGMAILER_BRAND_ID`；不设 `BIGMAILER_LIST_ID` 就发给品牌下全部列表，设了（可逗号分隔多个）就只发那些。
- `.github/workflows/newsletter.yml`：每天美东 8:00（AM）和 18:00（PM）自动构建、提交 View online 页面、推 BigMailer 草稿；也可手动触发并选择 `ready=true`。
- 文案、地址、广告位在 `content/newsletter/config.json` 里改。

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
  migrate_images.py  # 把旧站图片镜像到 src/static/uploads/
  radar.py           # 选题雷达（RSS + Google News，聚类打分）
  write_stories.py   # Claude 写稿 + 编辑审核 + 配图 + 发布为草稿
  fetch_person_photo.py / make_story_art.py / imgfit.py  # 配图
  seed_data.json     # 旧站快照
tests/test_app.py    # 端到端冒烟测试
Procfile / railway.json / runtime.txt / requirements.txt
.github/workflows/ci.yml
```
