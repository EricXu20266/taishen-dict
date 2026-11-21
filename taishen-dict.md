# 泰深词库（taishen-dict）— 项目宪法

> 本文件是项目级 L2 宪法。所有 AI 辅助开发与数据操作遵循此文件的约定。
> 与全局规则冲突时，以全局规则为准；与 IME 仓库宪法冲突时，词库侧以本文件为准。

## 项目信息

| 字段 | 值 |
|------|-----|
| 项目名称 | taishen-dict（泰深输入法词库构建管线） |
| 仓库 | https://github.com/EricXu20266/taishen-dict（**public**） |
| 下游消费方 | taishenIME（经 `tools/sync_to_ime.py` 单向同步） |
| 技术栈 | Python 3.11 + SQLite；数据源为 jieba / Wikipedia / THUOCL |
| 变更记录 | 本文件与 `docs/` 同步维护 |

## 职责边界

- `curate/` = 人工维护源（入库，**是词库唯一真相源**）
- `output/` = 纯构建产物（gitignore，不入库）
- `tmp/` = 临时语料与中间实验（gitignore，不入库）
- IME 仓库只消费 `output/` 同步产物，**不得在 IME 侧直接改词库**

改词条的唯一起点在这里。IME 侧改动必须回灌到 `curate/`，否则下次同步会被覆盖。

## curate/ 入口清单

| 文件 | 格式 | 作用 | pipeline 阶段 |
|---|---|---|---|
| `common_dict.txt` | `拼音<TAB>词`，行序=优先级 | 候选排序第一层 | 独立加载 |
| `boost.yaml` / `demote.yaml` | `词: 倍数/除数` | 频次调校 | 4.x |
| `fragments.txt` | 每行一词 | 碎片词频次压到 1 | 4.3 |
| `longwords.txt` | `词<TAB>拼音<TAB>频次` | 补长词 | 4.15 |
| `extra_words.txt` | `词<TAB>拼音<TAB>频次` | 补单字 / 补词条（已存在则跳过） | 4.16 |
| `pinyin_fix.txt` | `词<TAB>正确拼音` | 覆盖注错的拼音 | 4.17 |
| `extra_readings.txt` | `字<TAB>拼音<TAB>频次` | 单字补读音，输出阶段追加独立行 | 4.18 |
| `domains/` | `词 拼音` | 领域词源 | 5.4 |

**为什么补读音要单独一个入口**：主流程的 `merged` 是 `word → 单拼音` 的字典，一个词只挂一个读音。多音字（长 chang/zhang、似 shi/si）在这种结构下装不下，所以补读音绕开它，在输出阶段追加独立行；修拼音则在 `merged` 里直接覆盖。

**重建开关**：

- `python pipeline.py` — 全量（重新采集 wiki，数小时）
- `python pipeline.py --reuse-wiki` — 复用 `output/domains/*.txt` 跳过网络采集（约 30 秒），**只改 curate 层时用这个**
- 复用模式只取 `domains.yaml` 定义的 wiki 领域；THUOCL 等其他域不并入 system_dict，否则专名会污染常用候选

**同步后下游必须重建 bin 索引**，否则引擎加载旧索引、修正不生效：

```
build_index.exe resources\system_dict.db resources\system_dict.db.bin
```

**拼音格式**：条目拼音按音节分隔（`zhong'guo`），单音节词不带分隔符。手写 curate 源时统一用分隔格式——查询侧会做归一化，但索引键按原串建，混用两种格式会让同一词出现两个索引键。

**文件格式约定**：`curate/` 下源文件统一 **UTF-8 + LF** 行尾。历史遗留不统一（`pinyin_fix.txt` / `extra_readings.txt` 为 CRLF，`extra_words.txt` / `common_dict.txt` / `boost.yaml` 为 LF），改动这些文件时顺手统一，避免混合行尾导致「只加一行却整文件 diff」。

## 校验链路（发布前必过）

1. 词库侧 `tools/verify_build.py`：双表存在、简繁不混、common rank 连续、bigram 自洽
2. `tools/sync_to_ime.py`：同步前对产物哈希 == VERSION 清单，同步后对 IME 侧哈希
3. IME 侧 `tools/verify_resources.py`

任一步失败不得发布。

## 推送策略

- **指令推送**——用户说「推送」才 `git push`。
- 推送前必须通过下方安全隐私闸门。

## 推送前安全隐私闸门（硬约束）

本仓库是 **public**。任何 push / tag / 上传资产之前，必须先过这道闸门；任一项不通过即禁止推送。

**一、禁止上传的内容**

| 类别 | 具体 |
|------|------|
| 未脱敏语料 | 会话库导出、思维块/正文原文、论坛或新闻原文、wiki dump 文本、语料切片与分片 |
| 数据文件 | `*.db` / `*.db.bak*` / `*.pkl` / `*.tsv` / `*.log`（只允许入库 `curate/` 源与代码） |
| 个人信息 | 真实姓名与私人称呼（阿 X / X 总）、手机号、邮箱、证件号、住址、账号、**具体持仓与关注的标的** |
| 凭据 | 密钥、token、cookie、`.env`、私钥、带鉴权的 URL |
| 对话原文 | 任何文档、注释、commit message 中引用用户原话或 AI 原文 |

**二、词条本身也是公开数据**

`curate/` 下的每一行都会以公开文本形式进入版本库。从自有语料挖出的词条必须通过词条级审查：

- 私人称呼、联系人代号、家庭与工作关系称谓 → 一律不收
- 具体持仓、关注标的、交易计划相关个股名 → 一律不收（行业术语与领域词可以收）
- 论坛 ID、账号、内部项目代号 → 一律不收
- 只有「语义独立的通用词、术语、领域词」才可入库

**三、描述语料来源时的写法**

- 只写抽象表述：「内部语料」「自有语料」。
- 不写内部数据库路径、表名、字段名与数据规模，统一用「内部语料」代称。
- 词库源文件头部注释同样适用——注释也是公开内容。

**四、推送前必跑的检查（三项全过才允许 push）**

```powershell
# 1. 有没有数据文件/语料中间件进了版本库
git ls-files | Select-String -Pattern '\.(db|db\.bak|pkl|tsv|sqlite)$|corpus|dump|ngram'

# 2. 有没有凭据与隐私模式
git grep -n -I -E 'sk-[A-Za-z0-9]{16,}|ghp_|github_pat_|AKIA[0-9A-Z]{16}|password|Bearer [A-Za-z0-9]'

# 3. 本次待推内容逐文件过目
git diff --name-only origin/master..HEAD
```

第 3 步不能只看文件名——新文件与改动文件都必须读一遍实际内容，再决定是否推。

**五、本机路径**

文档与脚本中避免写死 `E:\AllinDeepSeek\...` 绝对路径；确需示例时用 `<项目根>` 占位。
