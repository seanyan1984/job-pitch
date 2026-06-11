# boss-pitch

BOSS 直聘精品投递工具 — 不海投，精准出击。

**CLI 负责数据采集和发送，AI 层通过 [SKILL.md](./SKILL.md) 文档驱动，任何 Agent 工具（Hermes、Cursor、Claude Code 等）都能执行。**

## 快速开始

```bash
# 安装
pip install boss-pitch
# 或
uv tool install .

# 1. 登录
boss-pitch login

# 2. 添加岗位
boss-pitch add "https://www.zhipin.com/job_detail/xxx.html"

# 3. 搜索（可选，或直接 add）
boss-pitch search "AI产品经理" --city 杭州 --salary 20-40K

# 4. 挑选
boss-pitch pick 1 3 5-8

# 5. 采集详情
boss-pitch scrape

# 6. AI 生成话术（由 Agent 读 SKILL.md 执行，不在 CLI 中）
# Agent 读取 profile.yaml + JD 详情 → 调 LLM → 写入 pitches.md

# 7. 发送
boss-pitch send           # 发送所有已审核话术
boss-pitch send 1 3 5     # 指定发送
boss-pitch send --dry-run  # 模拟

# 8. 验证送达
boss-pitch verify

# 查看状态
boss-pitch status
```

## 架构

```
┌─────────────────────────────────────────────┐
│                  Agent 层                    │
│  读 SKILL.md → 调 LLM → 读写 markdown 文件  │
│  （Hermes / Cursor / Claude Code / 任意）    │
│                                             │
│  init:  简历 PDF → profile.yaml             │
│  generate: JD + profile → pitches.md        │
│  review: 展示话术 → 用户审核 → 修改          │
└──────────────┬──────────────────────────────┘
               │ 读写 markdown 文件
┌──────────────▼──────────────────────────────┐
│                CLI 层 (本仓库)               │
│  boss-pitch add / search / pick / scrape    │
│  boss-pitch send / verify / status          │
│  boss-pitch login / config                  │
└─────────────────────────────────────────────┘
```

CLI 做数据采集和发送，不依赖 LLM。AI 能力通过 [SKILL.md](./SKILL.md) 文档描述，Agent 读文档后自行执行。

这样做的好处：
- CLI 零 LLM 依赖，安装即用
- AI 层不绑定特定 Agent，任何能读写文件+调 API 的工具都能用
- SKILL.md 是纯文档，人类也能读着手动操作

## 发送安全策略

- 每条消息间隔 8-12 秒（随机）
- 每批最多 4 条，批次间等 5 分钟（可配置）
- 连续 2 条失败自动停止
- 每条发送后做侧栏交叉验证（BOSS 会静默丢弃消息，对话内验证有假阳性）

## 数据存储

所有数据以 markdown 文件存储在 `~/.boss-pitch/jobs/{日期}/` 下：

```
~/.boss-pitch/
├── config.yaml          # CLI 配置
├── profile.yaml         # 候选人画像（init 生成）
├── chrome-data/         # Chrome 用户数据（登录态）
└── jobs/
    └── 2026-06-11/
        ├── search.md    # 搜索结果
        ├── picked.md    # 已选岗位
        ├── details/     # JD 详情
        ├── pitches.md   # 已生成的话术
        └── send-log.md  # 发送日志
```

零配置，人类可读，git 友好。

## 技术栈

- Python 3.10+
- Chrome CDP（直连，反爬友好）
- Markdown 文件存储
- Typer + Rich（CLI 界面）

## License

MIT
