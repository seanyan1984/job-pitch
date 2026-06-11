# boss-pitch AI Skills

AI 层不在 CLI 代码中，由 Agent 读写 markdown 文件 + 调用 LLM 执行。

任何能读写文件、调用 LLM API 的 Agent 工具都能用。

## 完整工作流

```
用户简历 → [init] → profile.yaml
搜索岗位 → search.md → picked.md → details/*.md
JD + Profile → [generate] → pitches.md
用户审核 → [review] → 修改后的 pitches.md
发送话术 → CLI: boss-pitch send
验证送达 → CLI: boss-pitch verify
```

---

## init — 从简历生成 profile.yaml

### 触发
- 用户提供简历文件（PDF/MD）
- 用户说"初始化 boss-pitch" / "导入简历"

### 流程

**1. 读取简历**

PDF 用 pymupdf 提取文本：
```python
import pymupdf
doc = pymupdf.open("简历.pdf")
text = "\n".join(page.get_text() for page in doc)
```

MD/TXT 直接读取。

**2. 调用 LLM 提炼结构化画像**

Prompt：
```
从以下简历中提取结构化信息，输出为 YAML 格式。只提取客观事实，不编造。

字段：
- name: 姓名
- title: 职业定位（一句话，如"AI产品经理"）
- years: 工作年限（数字）
- education: 学历（学校+专业+学位）
- skills: 核心技能列表（5-8个）
- highlights: 职业亮点列表（3-5条，每条一句话，带数字/成果）
- experience: 工作经历列表（公司+岗位+时间段+核心成果）
- style: 沟通风格偏好

只输出 YAML，不要解释。

简历内容：
{简历文本}
```

**3. 写入 ~/.boss-pitch/profile.yaml**

**4. 让用户确认/编辑**

---

## generate — 为 JD 生成话术

### 触发
- 用户说"生成话术" / "帮我写投递话术"

### 前置条件
- `~/.boss-pitch/profile.yaml` 已存在
- `~/.boss-pitch/jobs/{今天}/details/` 下有 JD 文件（`boss-pitch scrape` 生成）

### 流程

**1. 读取 profile.yaml**

**2. 读取 JD 详情**

details/ 下的文件是 YAML frontmatter + markdown 格式：
```yaml
---
company: XX公司
title: AI产品经理
boss_name: 陈女士
boss_role: HRBP
salary: 20-35K
---
岗位描述正文...
```

**3. 排除已有话术的 JD**

读取 pitches.md，跳过已生成的 job_id。

**4. 为每个 JD 生成话术**

称呼规则：
- CEO/创始人/总裁/VP/总监 → "X总"
- HR/HRBP/招聘专员 → "X女士/X先生"
- 角色不清 → "X总"（宁高不低）

Prompt：
```
你是一个求职话术写作专家。根据以下信息生成一条投递开聊话术。

【候选人画像】
{profile.yaml 内容}

【目标岗位】
公司：{company}
岗位：{title}
招聘人：{boss_name} ({boss_role})
JD 摘要：{jd_text 前 500 字}

【话术规则】
- 80-100字
- 称呼：{根据 boss_role 判断}
- 四段式：称呼+兴趣 → 能力匹配 → 成果佐证 → 收尾
- 不提公司品牌名（公司名没认知价值就不提）
- 不放个人使用数据（日均 5000 万 token 等放简历不放话术）
- 禁止 AI 味（不要"首先"、"总的来说"、"不可否认"等套话）
- 禁止"先说结论"式开头
- 中文必须有主语宾语，不写病句

直接输出话术文本，不要解释。
```

**5. 写入 pitches.md**

追加格式：
```markdown
## #{job_id} {company} · {title} · {boss_name}({boss_role})

{话术文本}

---
```

pitches.md 的完整格式见 `profiles/example.yaml` 同目录。

---

## review — 审核话术

### 触发
- 用户说"看一下话术" / "审核话术" / "修改第N条话术"

### 流程

**1. 读取 pitches.md**

**2. 逐条展示给用户**

格式：
```
#{id} {company} · {title} → {boss_name}
─────────────────────
{话术文本}
─────────────────────
字数: {len}字
```

**3. 用户可操作**
- "通过" / "没问题" → 跳过
- "改成 XXX" → 更新话术文本
- "删掉" → 从 pitches.md 中移除该条
- "重新生成" → 重新调 generate 的 LLM prompt

**4. 写回 pitches.md**

---

## 文件格式约定

所有数据文件在 `~/.boss-pitch/jobs/{YYYY-MM-DD}/` 下：

| 文件 | 格式 | 读写者 |
|------|------|--------|
| search.md | YAML frontmatter 列表 | CLI (search/add) |
| picked.md | 引用 search.md 的子集 | CLI (pick) |
| details/*.md | YAML frontmatter + JD 正文 | CLI (scrape) |
| pitches.md | Markdown 分隔的话术列表 | Agent (generate/review) |
| send-log.md | Markdown 表格 | CLI (send) |

profile.yaml 在 `~/.boss-pitch/profile.yaml`，Agent (init) 写入。

---

## Source of Truth 规则

- **pitches.md 是话术的唯一数据源**，不是 memory 也不是上下文
- 发送前必须从文件重新读取待发列表
- 用户的删除/修改操作以文件为准
- 重启/续接后必须重新读文件确认
