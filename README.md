# Everest Facet Watch

[中文](#中文) | [English](#english) | [नेपाली](#नेपाली)

## 中文

### 项目说明

Everest Facet Watch 是一个基于 Sentinel-1 SAR 的冰川陡坡面研究监测项目。它将固定坡面单元的雷达时序异常转换为供人工复核的排序快照。

**这不是灾害预警系统，也不会自动发送通知。** `critical`、`elevated` 和 `watch` 仅表示相对历史样本的研究性异常排名，不代表冰崩已经发生或即将发生。

### 当前流程

**Langtang 自动增量链**

```text
每小时检查 Google Earth Engine 的新下降轨 VV 场景
-> 仅写入未见过的 scene_id
-> SQLite 历史库
-> 同面片、同相对轨道评分
-> 轨道独立的最近三景统计
-> 原子发布 data/langtang.json
```

**Himalaya 两层链**

```text
固定面片资产
-> 全域年度下降轨 VV 季节均值 Tier 1
-> 覆盖校验
-> 最异常 Top 40 候选
-> 完整逐场景、逐轨道 Tier 2
-> 原子发布 data/himalaya.json
```

Himalaya 的两层设计来自 OCHA-DAP 的公开探索项目：全域 Tier 1 用于粗筛，Tier 2 只用于小候选集的精细验证，避免将大范围逐场景计算强行放进互动 API。

### 运行

1. 安装 Docker Desktop，并在 Google Cloud 项目中启用 Earth Engine。
2. 复制配置模板：

```powershell
Copy-Item .env.example .env
```

3. 在 `.env` 中设置你自己的可写 Earth Engine Asset 目录。
4. 在 `secrets/` 下完成 Earth Engine 本地授权。不要提交此目录中的凭据。
5. 启动：

```powershell
docker compose up -d --build
```

默认监控镜像标签为 `ghcr.io/yizebaba/everest-facet-watch:latest`。为保证可复现，请首次使用或更新代码时执行 `docker compose up -d --build`，它会从本仓库的独立 `python:3.12-alpine` Dockerfile 构建，不依赖任何其他项目镜像。发布者可在 [GitHub Container Package 设置](https://github.com/users/Yizebaba/packages/container/package/everest-facet-watch/settings)将 GHCR 包设为公开后，其他用户才可直接匿名拉取该标签。

访问地址：

- `http://localhost:8001/`
- `http://localhost:8001/langtang/`
- `http://localhost:8001/himalaya/`
- `http://localhost:8001/data/monitor-status.json`
- `http://localhost:8001/data/himalaya-status.json`
- `http://localhost:8001/data/himalaya-failures.json`

### 数据与安全

- `data/` 是本机运行数据，默认不提交：SQLite、场景统计、JSON 快照、状态与失败记录均在其中。
- `secrets/` 仅存放本机 Earth Engine 凭据，默认不提交。
- 自动清理仅删除超过 24 小时的 `*.tmp` 原子写入残留；不会删除 SQLite、固定面片、成功的 Tier 输出、页面快照或 Earth Engine Assets。
- 非空任务失败会写入 `himalaya-failures.json`，包含任务 ID、阶段、区块范围或年份及原始错误。`Table is empty.` 被单独记录为预期空区块。

### 方法限制

- 相对轨道独立计算，不把不同观察几何混入同一个连续三景统计。
- Sentinel-1 平台间辐射一致性尚未完成独立校准。
- Tier 1 是粗筛，不能替代 Tier 2，也不能作为预警。
- 仅在成功完成数据校验后替换页面 JSON；失败时保留上一份成功快照。

## English

### Overview

Everest Facet Watch is a research monitoring project for steep glacier facets using Sentinel-1 SAR time series. It turns radar anomalies for fixed terrain units into ranked snapshots for human review.

**It is not a warning system and sends no automated notifications.** `critical`, `elevated`, and `watch` are research rankings against historical samples. They do not confirm, predict, or announce a collapse.

### Pipelines

**Langtang incremental monitor**

```text
Hourly Google Earth Engine check for new descending VV scenes
-> deduplicated scene ingestion into SQLite
-> facet and relative-orbit scoring
-> orbit-separated recent three-observation statistic
-> atomic publication of data/langtang.json
```

**Himalaya two-tier monitor**

```text
Fixed facet assets
-> all-facet annual descending-VV seasonal-mean Tier 1 screen
-> completeness gate
-> Top 40 candidates
-> full per-acquisition, per-orbit Tier 2 validation
-> atomic publication of data/himalaya.json
```

The Himalaya workflow follows the OCHA-DAP public exploratory two-tier design: Tier 1 is a broad, coarse screen; Tier 2 performs the more expensive validation only for a small candidate set.

### Run

1. Install Docker Desktop and enable Earth Engine for a Google Cloud project.
2. Create a local configuration file:

```powershell
Copy-Item .env.example .env
```

3. Set a writable Earth Engine Asset folder in `.env`.
4. Authorize Earth Engine locally below `secrets/`. Never commit credentials.
5. Start the services:

```powershell
docker compose up -d --build
```

The default monitor image tag is `ghcr.io/yizebaba/everest-facet-watch:latest`. For reproducible setup, run `docker compose up -d --build`: it builds a standalone `python:3.12-alpine` image from this repository and does not depend on another project image. The publisher may make the GHCR package public in its [GitHub Container Package settings](https://github.com/users/Yizebaba/packages/container/package/everest-facet-watch/settings); only then can other users pull the tag anonymously.

Local URLs:

- `http://localhost:8001/`
- `http://localhost:8001/langtang/`
- `http://localhost:8001/himalaya/`
- `http://localhost:8001/data/monitor-status.json`
- `http://localhost:8001/data/himalaya-status.json`
- `http://localhost:8001/data/himalaya-failures.json`

### Data and retention

- `data/` contains local runtime products and is intentionally ignored: SQLite, observations, CSVs, JSON snapshots, health state, and failure records.
- `secrets/` contains local Earth Engine credentials and is intentionally ignored.
- Cleanup removes only atomic-write `*.tmp` leftovers older than 24 hours. It never removes databases, fixed facets, successful Tier outputs, snapshots, or Earth Engine Assets.
- Blocking Earth Engine failures are retained in `himalaya-failures.json` with task ID, phase, block bounds or year, and the original error. `Table is empty.` is retained separately as an expected empty block.

### Limitations

- Relative orbits are calculated separately; different viewing geometries are not mixed in a three-observation run.
- Sentinel-1 inter-platform radiometric harmonization is not yet implemented.
- Tier 1 is a screen, not a warning.
- A page JSON snapshot is replaced only after its validation gates pass. The last successful snapshot remains available after a failure.

## नेपाली

### परियोजना परिचय

Everest Facet Watch Sentinel-1 SAR समय-श्रृंखला प्रयोग गर्ने हिमनदीका ठाडा ढलान एकाइहरूको अनुसन्धान निगरानी परियोजना हो। यसले निश्चित ढलान एकाइका राडार असामान्यताहरूलाई मानव समीक्षाका लागि क्रमबद्ध स्न्यापसटमा रूपान्तरण गर्छ।

**यो पूर्वचेतावनी प्रणाली होइन र स्वचालित सूचना पठाउँदैन।** `critical`, `elevated`, र `watch` ऐतिहासिक नमूनासँग तुलना गरिएको अनुसन्धानात्मक श्रेणी मात्र हुन्। यसले हिमपहिरो भएको वा हुन लागेको पुष्टि गर्दैन।

### कार्यप्रवाह

**Langtang स्वचालित अद्यावधिक**

```text
हरेक घण्टा नयाँ घट्दो-कक्षा VV Sentinel-1 दृश्य खोज्ने
-> दोहोरिन नदिई SQLite मा भण्डारण
-> एउटै facet र एउटै relative orbit मा स्कोरिङ
-> कक्षा-अलग तीन-दृश्य तथ्याङ्क
-> data/langtang.json को सुरक्षित प्रकाशन
```

**Himalaya दुई-स्तरीय कार्यप्रवाह**

```text
स्थिर facet assets
-> सबै facet को वार्षिक descending-VV seasonal mean Tier 1 छनोट
-> पूर्णता जाँच
-> सबैभन्दा असामान्य Top 40 उम्मेदवार
-> प्रत्येक दृश्य र प्रत्येक कक्षाको Tier 2 प्रमाणीकरण
-> data/himalaya.json को सुरक्षित प्रकाशन
```

### चलाउने तरिका

```powershell
Copy-Item .env.example .env
docker compose up -d --build
```

पूर्वनिर्धारित monitor image tag `ghcr.io/yizebaba/everest-facet-watch:latest` हो। `docker compose up -d --build` चलाउँदा यो repository बाट स्वतन्त्र `python:3.12-alpine` image बनाइन्छ र अर्को परियोजनाको image चाहिँदैन। अन्य प्रयोगकर्ताले image सिधै pull गर्न GitHub Container Package लाई सार्वजनिक बनाउनु आवश्यक हुन्छ।

`.env` मा आफ्नो लेख्न मिल्ने Earth Engine Asset फोल्डर राख्नुहोस्। स्थानीय प्रमाणपत्र `secrets/` मा मात्र राख्नुहोस् र Git मा कहिल्यै नपठाउनुहोस्।

स्थानीय पृष्ठहरू:

- `http://localhost:8001/langtang/`
- `http://localhost:8001/himalaya/`

### सीमा र डेटा सुरक्षा

- यो अनुसन्धानात्मक निगरानी हो, सार्वजनिक चेतावनी होइन।
- फरक relative orbit का दृश्यहरू एउटै तीन-दृश्य तथ्याङ्कमा मिसाइँदैनन्।
- Sentinel-1 प्लेटफर्महरूबीचको radiometric calibration अझै पूरा भएको छैन।
- `data/` र `secrets/` सार्वजनिक repository मा समावेश हुँदैनन्।
- असफल कार्यहरू स्थान, वर्ष, कार्य ID र त्रुटिसहित `himalaya-failures.json` मा रेकर्ड हुन्छन्।

## Attribution

The scientific workflow is informed by the public OCHA-DAP exploratory work:

- <https://github.com/OCHA-DAP/ds-geospatial-impact-estimates>
- <https://ocha-dap.github.io/ds-geospatial-impact-estimates/langtang-sar-precursors/>

This repository is an independent local implementation. It is not an official OCHA product.
