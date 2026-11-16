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

```powershell
python tools/collect/wiki_corpus.py `
  --xml tmp/dumps/zhwiki-latest-pages-articles.xml.bz2 `
  --out-dir tmp/corpus_50k --limit 50000 --shard-mb 64
```

清洗只删结构性标记，不改写文字：模板/表格嵌套剥离、`ref`/`gallery` 标签删除、文件与分类链接删除、
`[[A|B]]`→B、外链取描述文字、粗体斜体标记去掉，最终**只保留汉字与换行**。
正文汉字数 < 200 的页面丢弃（`--min-han` 默认值）。

当前产物 `tmp/corpus_50k/`：

| 分片 | 体积 | 行数 |
|---|---|---|
| `corpus_000.txt` | 64.0 MB | 3,135,001 |
| `corpus_001.txt` | 64.0 MB | 3,074,127 |
| `corpus_002.txt` | 40.0 MB | 1,923,774 |
| 合计 | 168.1 MB | 8,132,902 |

### 与「完整 5 万页」的差异

2026-11-17 用同一脚本、同一 dump、同一组默认参数（`--limit 50000 --shard-mb 64 --min-han 200`）
完整重跑，实测结果：

```
页数 50000 / 保留 24046 / 分片 4 / 合计 201.1 MB
```

对照当前产物 168.1 MB / 3 片，**是完整 5 万页量的 83.6%**，且第三片（40 MB）未写满、
没有第四片。据此判断：当日提取在达到 5 万页限额**之前**就结束了（中断或被停），
实际只处理了约 4.2 万页。当日连跑日志未留存，无法确认是 Ctrl+C、超时还是其它原因。

结论：**当前语料是「dump 前约 4.2 万页」，不是完整 5 万页。**
若需要完整 5 万页（预计 +33 MB 语料、搭配对增加几个百分点），按上面的命令重跑一次即可。

---

## 3. 统计：语料 → bigram.db

脚本：`tools/build_context_prior.py`

```powershell
python tools/build_context_prior.py `
  --corpus tmp/corpus_50k `
  --dict output/system_dict.db `
  --out output/bigram.db `
  --top 700000 --min-count 2 --per-prev 8
```

2026-11-17 01:40 那次重建的实际输出（见 `tmp/build_bigram7.log`）：

```
词表: 355568 词（单字 7032）
语料文件: 3，候选搭配: 3105964（简繁剔除 2761 次、结构化对剔除 60638 次、双单字对剔除 19468577 次）
已写出 output/bigram.db（323152 对，19.6 MB）
频次区间: 2 ~ 8797
档位分布: {2: 210886, 3: 73508, 4: 19326, 5: 12109, 6: 7323}
前词数: 72764，双单字对占比: 0.0%
高档位(5-6)占比: 6.0%
```

> 清洗前的基线是 436,545 对 / 25.2 MB / 前词 95,333 / 双单字 11.4%。
> 清洗规则与复测结果见 `docs/bigram-acceptance-2026-11-17.md` 第 8 节。

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
| `--per-prev 8` | 每个前词最多 8 条**实词**搭配，提升前词覆盖率 |

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
档位 2 起全部可用（当时 436,545 对，可用对提升 80 倍；2026-11-17 清洗后为 323,152 对）。

消费侧代码：`taishenIME/engine/src/lib.rs` → `apply_context_boost`（排序键 = 档位降序 + 原位置）。

---

## 5. 重建步骤（完整链路）

```powershell
# 0) 前提：dump 已下载到 tmp/dumps/，且 output/system_dict.db 存在
#    （词表是分词的唯一依据，词库更新后应重建搭配表）

# 1) 提取语料（约 6 分钟：解压 3.2 GB bz2 + 清洗 5 万页）
python tools/collect/wiki_corpus.py --xml tmp/dumps/zhwiki-latest-pages-articles.xml.bz2 `
  --out-dir tmp/corpus_50k --limit 50000 --shard-mb 64

# 2) 统计并写出 bigram.db（约 6 分钟）
python tools/build_context_prior.py --corpus tmp/corpus_50k --dict output/system_dict.db `
  --out output/bigram.db --top 700000 --min-count 2 --per-prev 8

# 3) 登记版本（写 output/VERSION.json 的 dicts["bigram.db"]）
python pipeline.py

# 4) 校验
python tools/verify_build.py

# 5) 单向同步到 IME
python tools/sync_to_ime.py
```

等价的一键入口：`python tools/build_bigram.py`。

### 两个必须知道的坑

1. **`--corpus` 必须显式指定 `tmp/corpus_50k`**。`build_bigram.py` 的默认语料目录是
   `tmp/corpus`（不含 `_50k`），当前不存在；漏传会直接报「语料目录为空」。
   历史产物在 `tmp/corpus_50k`，`--reuse-corpus` 时要一起带上：
   `python tools/build_bigram.py --reuse-corpus --corpus tmp/corpus_50k`
2. **`--reuse-corpus` 不会清理旧分片**（只在提取分支里删 `corpus_*.txt`）。
   换 `--limit` 重跑时必须先手工清空目标目录，否则新旧分片会一起被统计。

---

## 6. 已知质量局限

2026-11-17 做了两轮抽样：清洗前 50 条（异常率 36%）、清洗后 50 条（异常率 6%）。
清洗规则见第 3 节质量约束表，详细对比见验收报告第 8 节。

清洗后的结构：

| 类别 | 占比 | 说明 |
|---|---|---|
| 全多字 | 100% | 双单字与单字后词已剔除，表内只剩多字对（前词可含单字高频字） |
| 档位 2 | 65% | 以虚词后词为主（`X 的`、`X 是`），拆组排序有用、主排序权重最低 |
| 高档位（5-6） | 6.0% | 抽检 10/10 与 7/10 为有效搭配 |

**根因未解决，只是被过滤掉**：碎片来自词库本身把专名切成独立词条
（`马尔马`、`大维`、`楚考烈`、`临川`、`中发` 都在 `system_dict` 里，词频 700-1700），
而完整词（`马尔马拉海`、`楚考烈王`、`单元音`、`不适当`）都不在词表。
正向最大匹配如实切出碎片，搭配表只能事后过滤。

彻底解决要在词库侧清理碎片词条或提高词表覆盖，那属于 `curate/` 的职责，不是本表能做的。

清洗后残留的 3 条碎片样本（50 条抽样中）：`克拉斯→车站`、`董事→兼行`、`卡利→古拉`
——都是**多字碎片**前词，靠单字规则拦不住；继续加规则会伤及正常词，移交给词库侧。

---

## 7. 复现性判定

- 语料可得：dump 在 `tmp/dumps/`（3.20 GB），提取脚本与参数齐全 → **可按本文档重建**
- 但 dump 是 `latest` 滚动快照，重新下载会得到**不同内容**，无法做到字节级复现
- 精确复现当前 `bigram.db` 的唯一前提是**保住 `tmp/corpus_50k/` 这三个分片**（168.1 MB）
  或用当前 dump 原样重跑

因此：`tmp/corpus_50k` 应视作**基线语料**保留，不要清空。
若确认要长期基线化，建议把三个分片打包后放入可访问的归档位置（换机器/换盘不丢）。
