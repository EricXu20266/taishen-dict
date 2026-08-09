# 泰深词库（taishen-dict）

泰深输入法的独立词库构建管线。产出 `system_dict.db`（系统词库）和 `domains/*.txt`（领域词库），供 [taishenIME](https://github.com/EricXu20266/taishenIME) 消费。

## 设计原则

- **独立于输入法 App**：词库有自己的迭代周期（月度更新），不与 taishenIME 发布绑定
- **许可干净**：所有数据源可商业使用（MIT / CC BY-SA）
- **可人工干预**：`curate/boost.yaml` 和 `curate/demote.yaml` 随时调整词频，不需改代码
- **领域可扩展**：加一行 `domains.yaml` 即可新增领域

## 目录结构

```
taishen-dict/
├── pipeline.py              # 一条命令：采集 → 清洗 → 注音 → 融合 → 输出
├── domains.yaml             # 领域 → 维基百科分类映射（增删领域改这个）
├── curate/
│   ├── boost.yaml           # 打字高频词加成表（Eric 手动维护）
│   └── demote.yaml          # 新闻虚高词降权表
├── sources/
│   ├── jieba.py             # jieba 词典采集（MIT）
│   └── wiki.py              # Wikipedia 领域词条采集（CC BY-SA）
├── output/
│   ├── sqlite.py            # 生成 system_dict.db
│   ├── domains.py           # 按领域导出 txt
│   ├── domains_db.py        # domains/*.txt → domains.db（引擎 V0.5+ 优先加载）
│   ├── system_dict.db       # ← 构建产物（gitignore）
│   ├── domains/             # ← 构建产物（gitignore）
│   │   └── domains.db       # ← 构建产物（gitignore）
└── tmp/                     # 缓存（gitignore）
```

## 用法

### 前置条件

```bash
pip install pypinyin pyyaml
```

### 构建

```bash
python pipeline.py
```

一条命令跑完全量：下载 jieba 词典 → 采集 Wikipedia 领域词条 → 注音 → 频次调校 → 输出 system_dict.db + domains/*.txt + domains.db。

### 输出

- `output/system_dict.db` — SQLite 系统词库（pinyin / word / frequency）
- `output/domains/*.txt` — 领域词库（词\t拼音，源数据）
- `output/domains/domains.db` — 领域词库 SQLite（引擎 V0.5+ 优先加载）

复制到 taishenIME 的 `resources/` 目录即可。

## 数据源与许可

| 源 | 内容 | 许可 | 商业可用 
|----|------|------|---------
| [jieba](https://github.com/fxsjy/jieba) | 34.9 万词条 + 词频 | MIT | ✅ 
| [中文维基百科](https://zh.wikipedia.org) | 领域分类词条 | CC BY-SA 4.0 | ✅（需署名） 
| [pypinyin](https://github.com/mozillazg/python-pinyin) | 汉字拼音注音 | MIT | ✅ 

## 领域列表

20 领域（`domains.yaml`）：计算机 / 数学 / 物理 / 化学化工 / 生物 / 地理地质 / 天文 / 气象 / 成语 / 医学 / 法律 / 经济金融 / 哲学 / 历史 / 文学 / 体育 / 军事 / 农业 / 艺术 / 饮食 / 心理学

## 频次调校

jieba 词频基于新闻语料——"发展""推进"排前列，"哈哈""干嘛"靠后。两份 YAML 纠正偏差：

- `boost.yaml`：打字高频词给频次乘倍数
- `demote.yaml`：新闻虚高词给频次除除数

修改后重跑 `python pipeline.py` 即可生效。不需要懂代码。

## License

代码：MIT  
词库数据：MIT（jieba 部分）+ CC BY-SA 4.0（Wikipedia 部分，署名中文维基百科）
