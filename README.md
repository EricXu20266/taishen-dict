# 泰深词库（taishen-dict）

泰深输入法的独立词库构建管线。产出 `system_dict.db`（系统词库）、`domains.db`（领域词库）和 `common.db`（常用词库），供 [taishenIME](https://github.com/EricXu20266/taishenIME) 消费。

## 设计原则

- **独立于输入法 App**：词库有自己的迭代周期（月度更新），不与 taishenIME 发布绑定
- **许可干净**：所有数据源可商业使用（MIT / CC BY-SA）
- **可人工干预**：`curate/boost.yaml` 和 `curate/demote.yaml` 随时调整词频，`curate/common_dict.txt` 直接排候选优先级，都不需改代码
- **领域可扩展**：加一行 `domains.yaml` 即可新增领域
- **构建可校验**：pipeline 末尾校验阀门断言简繁分集完整性，失败中止构建，防"混在一起"的脏词库进 IME

## 目录结构

```
taishen-dict/
├── pipeline.py              # 一条命令：采集 → 清洗 → 注音 → 融合 → 简繁分集 → 输出 → 校验
├── domains.yaml             # 领域 → 维基百科分类映射（增删领域改这个）
├── curate/                     # 人工维护源（入库）——语义：一切不可重建的人工数据
│   ├── boost.yaml           # 打字高频词加成表（Eric 手动维护）
│   ├── demote.yaml          # 新闻虚高词降权表
│   ├── common_dict.txt      # 常用词优先级表（pinyin<TAB>word，行序=优先级，P2 层）
│   └── domains/             # 领域源 txt（14 个，不可重建：thuocl_* 11 + conversation/modern/network_slang）
├── sources/
│   ├── jieba.py             # jieba 词典采集（MIT）
│   └── wiki.py              # Wikipedia 领域词条采集（CC BY-SA）
├── output/                    # 纯构建产物（gitignore）——语义：一切可由 pipeline 重建的
│   ├── sqlite.py            # 生成 system_dict.db（简体 + 繁体原文双表）
│   ├── domains.py           # 按领域导出 txt
│   ├── domains_db.py        # domains/*.txt → domains.db（简繁双表，引擎 V0.5+ 优先加载）
│   ├── system_dict.db       # ← 构建产物（gitignore）
│   ├── common.db            # ← 构建产物（gitignore）
│   ├── domains/             # 21 wiki 领域 txt（重建产物）+ 14 源 txt（构建时从 curate 合并）+ domains.db
│   │   └── domains.db       # ← 构建产物（gitignore）
├── tools/
│   ├── build_common_db.py   # curate/common_dict.txt → common.db（rank 行序）
│   ├── verify_build.py      # 校验阀门：双表存在 + 简体表无繁体混入 + common rank 连续
│   ├── collect_network_slang.py  # 维基网络流行语独立采集（旁路，写入 domains/network_slang.txt）
│   └── fix_domains.py       # 快速重建单个领域 txt（调试用）
└── tmp/                     # 缓存（gitignore）
```

## 用法

### 前置条件

```bash
pip install pypinyin pyyaml zhconv
```

### 构建

```bash
python pipeline.py
```

一条命令跑完全量：下载 jieba 词典 → 采集 Wikipedia 领域词条 → 注音 → 频次调校 → 简繁分集 → 输出 system_dict.db + domains/*.txt + domains.db + common.db → 校验阀门 → 版本清单。

### 同步到 IME

```bash
python tools/sync_to_ime.py
```

按 `output/VERSION.json` 清单同步到 taishenIME：同步前对账（产物哈希与清单一致）→ 复制 3 个 db + VERSION + common_dict.txt + 35 个领域 txt → 同步后对账（IME 侧哈希确认）。

### 输出

- `output/system_dict.db` — 系统词库 SQLite，双表：`system_dict`（简体，pinyin/word/frequency）+ `system_dict_trad`（繁体原文）
- `output/domains/*.txt` — 领域词库 txt（21 个 wiki 领域由 pipeline 重建，14 个源 txt 构建时从 `curate/domains/` 合并，构建后目录共 35 个）
- `output/domains/domains.db` — 领域词库 SQLite，双表：`domain_words` + `domain_words_trad`
- `output/common.db` — 常用词库 SQLite：`common_words(rank, pinyin, word)`，rank 行序即 P2 层优先级
- `output/VERSION.json` — 版本清单：版本号 + git commit + 各词库 sha256/条数（同步脚本据此对账）

## 词库版本控制

- **版本号**：`V{YYYY}.{MM}.{DD}.{n}`（同日构建自动递增序号），pipeline 校验通过后生成
- **VERSION.json**：每次构建记录版本号、git commit、三个 db 的 sha256 与表条数
- **同步即对账**：`sync_to_ime.py` 同步前校验产物哈希 == 清单（防产物被改/清单过期），同步后校验 IME 侧哈希 == 清单（防复制不完整）
- **IME 侧记录**：`taishenIME/resources/VERSION.json` 存当前词库版本，随同步更新

## 数据源与许可

| 源 | 内容 | 许可 | 商业可用
|----|------|------|---------
| [jieba](https://github.com/fxsjy/jieba) | 34.9 万词条 + 词频 | MIT | ✅
| [中文维基百科](https://zh.wikipedia.org) | 领域分类词条 | CC BY-SA 4.0 | ✅（需署名）
| [pypinyin](https://github.com/mozillazg/python-pinyin) | 汉字拼音注音 | MIT | ✅
| [THUOCL](https://github.com/thunlp/THUOCL) | 13 领域专业词（thuocl_*.txt） | MIT | ✅

## 领域列表

21 领域（`domains.yaml`）：计算机 / 数学 / 物理 / 化学化工 / 生物 / 地理地质 / 天文 / 气象 / 成语 / 医学 / 法律 / 经济金融 / 哲学 / 历史 / 文学 / 体育 / 军事 / 农业 / 艺术 / 饮食 / 心理学

另含 THUOCL 13 领域 + conversation / modern / network_slang 补充源（手工维护，存于 `curate/domains/`，构建时合并进产物）。

## 频次调校

jieba 词频基于新闻语料——"发展""推进"排前列，"哈哈""干嘛"靠后。两份 YAML 纠正偏差：

- `boost.yaml`：打字高频词给频次乘倍数
- `demote.yaml`：新闻虚高词给频次除除数

修改后重跑 `python pipeline.py` 即可生效。不需要懂代码。

## 校验阀门

`tools/verify_build.py` 在 pipeline 末尾自动运行（失败退出码 1 中止构建）：

- system_dict.db / domains.db 双表必须存在
- 简体表不允许出现大规模繁体混入（GB2312 之外的繁体独有字覆盖词条数 ≥100 即 FAIL；零星生僻人名 WARN 不阻断）
- common.db rank 必须 0..n-1 连续且与源 txt 条数对账一致

## License

代码：MIT
词库数据：MIT（jieba 部分）+ CC BY-SA 4.0（Wikipedia 部分，署名中文维基百科）
