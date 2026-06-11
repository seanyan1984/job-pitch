# job-pitch

**求职自荐 — BOSS 直聘 AI 个性化投递工具**

## 为什么做这个

中年二次被裁后的一次自救尝试。

之前一次投简历投了四个月，海投效率太低——每天发 50 条"你好，我对这个岗位很感兴趣"，回复率不到 5%。这次自己做了个工具，一个月成功找到新下家。双休，待遇也还过得去。

BOSS 直聘的机制是**必须打招呼才能投递**，打招呼的话术就是你的简历封面。一条好的话术能让招聘者点进你的简历，一条垃圾话术直接被划走。海投的人用的都是模板，"您好我对贵公司XX岗位非常感兴趣，期待与您进一步沟通"——这种话招聘者一天收 200 条，根本不会看。

所以这个工具做的是：精选岗位 → 采集 JD → 根据你的简历和岗位要求生成个性化话术 → 精准发送 → 验证送达。

一天精准投 20-30 个，AI 帮你认真写，机会很快会来。

## 怎么工作

```
你的简历 → profile.yaml
     ↓
BOSS 直聘 → 精选岗位 → 采集 JD 详情
     ↓
profile + JD → LLM 生成个性化话术（80-100字）
     ↓
审核话术 → 发送 → 验证送达
```

核心思路：**话术质量 >> 投递数量**。用 LLM 把你的经历和岗位要求做深度匹配，生成有针对性的开头，让招聘者愿意点进来看。

## 安装

```bash
pip install job-pitch
# 或
uv tool install .
```

需要 Chrome 或 Edge 浏览器。通过 CDP（Chrome DevTools Protocol）直连已打开的浏览器操作，不是无头浏览器——用的是你真实登录的浏览器环境，更安全，不容易被反爬检测到。

## 使用

### CLI 部分：数据采集 + 发送

```bash
# 登录 BOSS 直聘（扫码）
boss-pitch login

# 添加岗位（从浏览器复制 URL）
boss-pitch add "https://www.zhipin.com/job_detail/xxx.html"

# 或搜索
boss-pitch search "AI产品经理" --city 杭州 --salary 20-40K

# 挑选感兴趣的
boss-pitch pick 1 3 5-8

# 采集 JD 详情
boss-pitch scrape

# 发送话术
boss-pitch send           # 发送所有
boss-pitch send 1 3       # 指定发送
boss-pitch send --dry-run  # 模拟（不实际发）

# 验证送达（BOSS 会静默丢弃消息，这个工具做侧栏交叉验证）
boss-pitch verify

# 查看状态
boss-pitch status
```

### AI 部分：生成话术

AI 层不在 CLI 代码中，而是通过 [SKILL.md](./SKILL.md) 文档描述。任何支持读写文件 + 调用 LLM 的 Agent 工具都能执行：

- **Hermes Agent**：把 `SKILL.md` 放到 skills 目录，直接触发
- **Cursor / Claude Code**：把 `SKILL.md` 加到项目上下文，让 agent 按文档执行
- **其他工具**：照着文档手动操作也行

AI 层做三件事：

1. **init**：从简历 PDF 提取结构化画像 → `~/.boss-pitch/profile.yaml`
2. **generate**：读 profile + JD 详情，调 LLM 生成 80-100 字的个性化话术 → `pitches.md`
3. **review**：展示话术让用户审核，支持修改/删除/重新生成

```bash
# 以 Hermes Agent 为例（其他 agent 类似）：
# 1. 让 agent 读 SKILL.md
# 2. "帮我初始化简历" → agent 执行 init 流程
# 3. "生成话术" → agent 执行 generate 流程
# 4. agent 展示话术，你说"通过"或"改成XXX"
# 5. 确认后用 CLI 发送：boss-pitch send
```

这样设计是因为不想把 AI 层绑死到某个 LLM 提供商或 Agent 框架。SKILL.md 是纯文档，你用什么工具都行，甚至手动写 `pitches.md` 也完全没问题。

## 发送安全

- 每条消息间隔 8-12 秒（随机）
- 每批最多 4 条，批次间隔 5 分钟
- 连续 2 条失败自动停止
- 每条发送后做侧栏交叉验证（BOSS 会静默丢弃消息，普通验证有假阳性）

## 和海投工具的区别

| | job-pitch | 海投工具 |
|---|---|---|
| 策略 | 精选 3-5 个/天 | 批量 50-100 个/天 |
| 话术 | LLM 深度匹配简历+JD | 模板变量替换 |
| 发送验证 | 侧栏交叉确认送达 | 发完就跑 |
| 思路 | 质量优先 | 数量优先 |

两种策略没有绝对优劣，看个人情况。我投到后期发现，精心准备的 5 条比乱发的 50 条回复率高很多，所以才做了这个工具。

## 数据存储

所有数据以 markdown 文件存在 `~/.boss-pitch/jobs/{日期}/` 下，人类可读，零配置。

## License

MIT
