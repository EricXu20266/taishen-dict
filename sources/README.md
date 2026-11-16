# sources/ 说明：搭配表（bigram）语料记录

本文件记录 `output/bigram.db`（引擎表名 `context_prior`）背后的**语料来源、统计口径与重建步骤**。
词条（系统词库 / 领域词 / 常用词）的采集方法论见 [`docs/COLLECTION.md`](../docs/COLLECTION.md)，
本文件只覆盖**正文语料 → 相邻词共现**这条链路。

验收结论与质量数据见 [`docs/bigram-acceptance-2026-11-17.md`](../docs/bigram-acceptance-2026-11-17.md)。

---

## 1. 语料来源

| 项 | 值 |
|---|---|
| 数据集 | 中文维基百科条目正文（`zhwiki-latest-pages-articles`） |
| URL | `https://dumps.wikimedia.org/zhwiki/latest/zhwiki-latest-pages-articles.xml.bz2` |
| 本地路径 | `tmp/dumps/zhwiki-latest-pages-articles.xml.bz2`（`tmp/` 不入库） |
| 体积 | 3.20 GB（压缩态） |
| 下载时间 | 2026-10-29 12:54（文件 mtime，即当时 `latest` 快照的落地时刻） |
| 生成器 | MediaWiki 1.47.0-wmf.21（dump 内 `<siteinfo>`） |
| 首篇样本 | `<title>Wikipedia:上载纪录/存档/2002年</title>`，revision id 82009188 |
| 许可 | 正文 CC BY-SA 4.0 + GFDL，署名「维基百科贡献者」 |

`latest` 是滚动链接，所以**无法从文件名反查确切快照日期**；dump 头部的 `<generationtimestamp>`
在当前版本中缺失，可用锚点只有上表的下载时间与 MediaWiki 版本号。

重新获取：

```powershell
# 4 连接稳定；8 线程会被 Wikimedia 429
aria2c -c -x4 -s4 -m10 --file-allocation=none `
  -o zhwiki-latest-pages-articles.xml.bz2 `
  https://dumps.wikimedia.org/zhwiki/latest/zhwiki-latest-pages-articles.xml.bz2
```

---

## 2. 提取：dump → 纯文本语料

脚本：`tools/collect/wiki_corpus.py`（流式 bz2 解压，按 `<page>` 边界切分，内存恒定）

提取与统计的完整命令：

```powershell
# 提取（约 200 秒）
python tools/collect/wiki_corpus.py `
  --xml tmp/dumps/zhwiki-latest-pages-articles.xml.bz2 `
  --out-dir tmp/corpus_50k_full --limit 50000 --shard-mb 64 --min-han 200

# 统计（约 4 分钟）
python tools/build_context_prior.py `
  --corpus tmp/corpus_50k_full --dict output/system_dict.db --out output/bigram.db `
  --top 700000 --min-count 2 --per-prev 10
```

清洗只删结构性标记，不改写文字：模板/表格嵌套剥离、`ref`/`gallery` 标签删除、文件与分类链接删除、
`[[A|B]]`→B、外链取描述文字、粗体斜体标记去掉，最终**只保留汉字与换行**。
正文汉字数 < 200 的页面丢弃（`--min-han` 默认值）。

当前基线语料 `tmp/corpus_50k_full/`（2026-11-17 补齐到 5 万页）：

| 分片 | 体积 | 说明 |
|---|---|---|
| `corpus_000.txt` | 64.0 MB | |
| `corpus_001.txt` | 64.0 MB | |
| `corpus_002.txt` | 64.0 MB | |
| `corpus_003.txt` | 9.1 MB | 5 万页限额在此处停止 |
| 合计 | 201.1 MB | 保留 24,046 页，产出 385,983 对 |

历史目录 `tmp/corpus_50k/`（168.1 MB / 3 片）是 2026-10-29 那次提取的产物，
当时在约 4.2 万页处被中断（第三片只写到 40 MB）。已用前缀校验确认：
它的前两片与现基线逐字节相同，第三片是现 `corpus_002.txt` 的前缀——
即**补充是纯追加，不会改变已有搭配对**。
旧目录可作历史备份，不再作为构建输入。

---

## 3. 统计：语料 → bigram.db

脚本：`tools/build_context_prior.py`

```powershell
python tools/build_context_prior.py `
  --corpus tmp/corpus_50k_full `
  --dict output/system_dict.db `
  --out output/bigram.db `
  --top 700000 --min-count 2 --per-prev 10
```

2026-11-17 02:12 那次重建的实际输出（见 `tmp/build_perprev10.log`）：

```
词表: 355568 词（单字 7032）
语料文件: 4，候选搭配: 3556696（简繁剔除 3292 次、结构化对剔除 63617 次、双单字对剔除 23532851 次）
已写出 output/bigram.db（385983 对，23.4 MB）
频次区间: 2 ~ 10133
档位分布: {2: 248993, 3: 88534, 4: 24039, 5: 15185, 6: 9232}
前词数: 79306，双单字对占比: 0.0%
高档位(5-6)占比: 6.3%
```

> 演进：清洗前 436,545 对（4.2 万页）→ 清洗后 323,152 对 → 补齐 5 万页且
> `--per-prev` 提到 10 后 385,983 对。规则与复测见 `docs/bigram-acceptance-2026-11-17.md`
> 第 8 节与 `docs/bigram-acceptance-2026-11-17-2.md`。

**关于 `--per-prev`**：它是「每个前词最多保留多少条多字实词搭配」。语料越大，
同一前词的后继越多，配额越紧就越多长尾有效搭配被挤掉——2026-11-17 实测
`--per-prev 8` 会把「托勒密→十二」挤出（被「九世」挤占），导致验证集少 1 例。
语料继续增长时应同步上调（5 万页配 10 是经验值，10 万页建议 16-20）。

（词表 355568 是脚本内 `1 ≤ len ≤ 4` 过滤后的规模；`system_dict.db` 全表 372733 条。）

### 分词方式

**不是 jieba**。以 `system_dict.db` 的词表做**正向最大匹配**：

- 连续汉字段作为待分词片段（标点/空白/字母数字视为边界）
- 片段内从长到短匹配词表；匹配不到时按单字兜底
- 4 字以上整词再按同一词表还原内部边界（`resplit_inner`），额外补一层内部词对——
  且**保守口径只处理 ≥4 字整词**：3 字的「共产党」「共和国」不拆，避免把已有的
  （中国→共产党）打碎成（中国→共产）

### 质量约束

| 约束 | 作用 |
|---|---|
| `STOP_WORDS`（32 个单字虚词） | 前词是纯虚词时整对跳过（「的→X」无预测价值） |
| `WEAK_NEXT`（38 个虚/助词） | 后词是虚词时**保留但封顶 3 档**，且不占 `--per-prev` 配额 |
| 双单字对剔除 | 两边都是单字的对整对丢弃——碎片的最密集形态（档位 6 曾占 28.8%） |
| 后词单字（非虚词）剔除 | 专名尾字碎片（`马尔马→拉`、`单元→音`）；虚词单字保留，拆组排序需要 |
| 前词单字词频 < 3600 剔除 | 专名首字碎片（`芬→王朝`、`政→收入`）；`他/我/年/月/们`（3880+）不受影响 |
| `NOISE_PAIRS`（22 组） | 百科结构化标签对（`年→出生`、`总人口→为`）整对剔除 |
| 简繁过滤（默认开） | 含繁体独有字的词对剔除——简体模式下命中不到，统计了也没用 |
| `--per-prev 10` | 每个前词最多 10 条**实词**搭配；太小会挤掉长尾有效搭配（见第 3 节） |

三条单字规则可用 `--no-single-filter` 整体关闭（对比实验用）。

> 不要用「降档到 2」代替「剔除」：消费侧判据是 `lift >= 2`，档位 2 照样生效。
> 实测降档会让碎片从高档位涌进档位 2，档位 2 的切碎率从 10% 升到 50%，比不处理更差。

---

## 4. 档位映射口径（与消费侧严格对齐）

`context_prior.count` 存的是**档位 1-6**，不是原始共现次数。

| 档位 | 原始共现次数 | 消费侧行为 |
|---|---|---|
| 1 | 1 次 | 无证据，不进主排序（`--min-count 2` 使该档不落库） |
| 2 | 2-3 次 | 起算权重 |
| 3 | 4-7 次 | |
| 4 | 8-15 次 | |
| 5 | 16-39 次 | |
| 6 | 40 次及以上 | 最强权重 |

后词属于 `WEAK_NEXT` 时：≤7 次记 2 档，8 次以上记 3 档（弱证据封顶）。

**为什么改绝对频次映射**：早期用分位切分（按排名百分比分档），40 万行里 98.6% 被压成 1 档，
消费侧当作无证据丢弃——数据库 40 万行、实际可用仅 5,453 对。改成绝对频次映射后，
档位 2 起全部可用（当时 436,545 对，可用对提升 80 倍；2026-11-17 清洗并补齐语料后为 385,983 对）。

消费侧代码：`taishenIME/engine/src/lib.rs` → `apply_context_boost`（排序键 = 档位降序 + 原位置）。

---

## 5. 重建步骤（完整链路）

```powershell
# 0) 前提：dump 已下载到 tmp/dumps/，且 output/system_dict.db 存在
#    （词表是分词的唯一依据，词库更新后应重建搭配表）

# 1) 提取语料（约 200 秒：解压 3.2 GB bz2 + 清洗 5 万页）
python tools/collect/wiki_corpus.py --xml tmp/dumps/zhwiki-latest-pages-articles.xml.bz2 `
  --out-dir tmp/corpus_50k_full --limit 50000 --shard-mb 64 --min-han 200

# 2) 统计并写出 bigram.db（约 4 分钟）
python tools/build_context_prior.py --corpus tmp/corpus_50k_full --dict output/system_dict.db `
  --out output/bigram.db --top 700000 --min-count 2 --per-prev 10

# 3) 登记版本（写 output/VERSION.json 的 dicts["bigram.db"]）
python tools/update_version.py --dicts bigram.db

# 4) 校验
python tools/verify_build.py

# 5) 单向同步到 IME
python tools/sync_to_ime.py

# 6) 部署到安装目录（IME 实际读的是这里）
Copy-Item <taishenIME>\resources\bigram.db "$env:LOCALAPPDATA\TaishenIME\bigram.db" -Force
```

等价的一键入口：`python tools/build_bigram.py`。

### 两个必须知道的坑

1. **`--corpus` 必须显式指定 `tmp/corpus_50k_full`**。`build_bigram.py` 的默认语料目录是
   `tmp/corpus`（不含后缀），当前不存在；漏传会直接报「语料目录为空」。
   现基线在 `tmp/corpus_50k_full`，`--reuse-corpus` 时要一起带上：
   `python tools/build_bigram.py --reuse-corpus --corpus tmp/corpus_50k_full`
2. **`--reuse-corpus` 不会清理旧分片**（只在提取分支里删 `corpus_*.txt`）。
   换 `--limit` 重跑时必须先手工清空目标目录，否则新旧分片会一起被统计。

---

## 6. 已知质量局限

2026-11-17 做了三轮抽样：清洗前 50 条（异常率 36%）、清洗后 50 条（异常率 6%）、
补齐 5 万页后 50 条（异常率 4%）。清洗规则见第 3 节质量约束表，
详细对比见 `docs/bigram-acceptance-2026-11-17.md` 第 8 节与 `-2.md` 第 3 节。

清洗后的结构：

| 类别 | 占比 | 说明 |
|---|---|---|
| 全多字 | 100% | 双单字与单字后词已剔除，表内只剩多字对（前词可含单字高频字） |
| 档位 2 | 64.5% | 以虚词后词为主（`X 的`、`X 是`），拆组排序有用、主排序权重最低 |
| 高档位（5-6） | 6.3% | 补齐后抽检 10/10 与 10/10 为有效搭配 |

**根因未解决，只是被过滤掉**：碎片来自词库本身把专名切成独立词条
（`马尔马`、`大维`、`楚考烈`、`临川`、`中发` 都在 `system_dict` 里，词频 700-1700），
而完整词（`马尔马拉海`、`楚考烈王`、`单元音`、`不适当`）都不在词表。
正向最大匹配如实切出碎片，搭配表只能事后过滤。

彻底解决要在词库侧清理碎片词条或提高词表覆盖，那属于 `curate/` 的职责，不是本表能做的。

清洗后残留的多字碎片样本（50 条抽样中）：第 2 轮是 `克拉斯→车站`、`董事→兼行`、`卡利→古拉`，
补齐 5 万页后是 `禁卫→坦克`、`北安→大略` ——都是**多字碎片**前词，靠单字规则拦不住；
继续加规则会伤及正常词，移交给词库侧。

---

## 7. 复现性判定

- 语料可得：dump 在 `tmp/dumps/`（3.20 GB），提取脚本与参数齐全 → **可按本文档重建**
- 但 dump 是 `latest` 滚动快照，重新下载会得到**不同内容**，无法做到字节级复现
- 精确复现当前 `bigram.db`（385,983 对）的唯一前提是**保住 `tmp/corpus_50k_full/`
  的四个分片**（201.1 MB），或用当前 dump 原样重跑

因此：`tmp/corpus_50k_full` 应视作**基线语料**保留，不要清空。
建议把四个分片打包后放入可访问的归档位置（换机器/换盘不丢）；
`tmp/corpus_50k`（168.1 MB，4.2 万页版本）可作历史备份，不再作构建输入。
