# 抓取前标题预筛交付记录

开发基线：`QFgood/xianyu`，分支 `work`，HEAD `77f8d75869acd65f9dba72731b95c7c49ea67bdf`；开始时工作区干净，与方案基线一致。实现通过功能分支提交至 GitHub，并按用户授权通过 PR 合并；提交与合并状态以关联 PR 为准。未进行线上部署或预筛模式切换。真实 `config.json`、Criteria、Cookie、密钥与用户数据未修改。

## 改动范围

- `src/domain/models/prefilter.py`：严格、有限的通用字面量配置；存量坏字段单独降级。
- `src/services/listing_prefilter.py`：独立编译快照、纯标题判断、保留优先、稳定原因、限量日志与一次性汇总。
- `src/domain/models/task.py`、`src/services/task_generation_runner.py`：四类 DTO 与 AI/关键词生成透传，PATCH 省略保留、null 清除、对象替换，更新后保持嵌套模型类型。
- `src/infrastructure/persistence/{sqlite_connection,sqlite_task_repository,sqlite_bootstrap,json_task_repository}.py`：新增可空列，跨进程写事务串行迁移、WAL 首次协商的有界锁等待、往返、旧 JSON 导入与字段级容错。
- `src/scraper.py`：原去重之后、详情等待和新页面之前的唯一门禁；外层 finally 汇总失败/取消/暂停/中断状态，保持原清理及 dispatcher join。
- `spider_v2.py`：改用 `model_dump()`，SQLite 与显式 JSON 字段均到达实际 scraper。
- `README.md`、`config.json.example`、`examples/`、`tools/replay_listing_prefilter.py`：默认关闭、shadow 样例、独立离线回放、启停与回滚。
- 测试：新增规则、存储、真实 scraper/dispatcher 回放，扩展 API 生成和 PATCH 契约测试。

未增加 Web 表单。现有 TaskForm 对初始任务使用对象展开，提交保留额外字段；即使调用方省略字段，API 也保留已存规则。普通编辑的保留契约已通过 API 回归；未做浏览器手工交互验收。

## 离线验证

依赖安装在 `/workspace/.venv`，未修改 requirements。定向命令：

```bash
/workspace/.venv/bin/python -m pytest -m 'not live and not live_slow' \
  tests/unit/test_listing_prefilter.py tests/unit/test_domain_task.py \
  tests/unit/test_item_analysis_dispatcher.py tests/unit/test_search_pagination.py \
  tests/integration/test_prefilter_storage.py tests/integration/test_scraper_prefilter.py \
  tests/integration/test_pipeline_parse.py tests/integration/test_api_tasks.py \
  tests/integration/test_cli_spider.py
```

结果：**127 passed**。

```bash
/workspace/.venv/bin/python -m pytest -m 'not live and not live_slow'
```

最终结果：**224 passed，3 deselected**。另用 `git archive HEAD` 导出未修改基线并复跑全量，结果 **126 passed，2 failed，3 deselected**；开发初期全量同样复现这两项既有失败，合并前已修正：

1. `tests/test_frontend_build_paths.py::test_frontend_build_output_path_is_consistent_across_configs`：移除 `.dockerignore` 中遗留的 `web-ui/dist`，构建统一使用根目录 `dist/`。
2. `tests/unit/test_utils.py::test_save_to_jsonl`：结果读取已附加 `_status` 等元数据，更新旧测试以明确断言完整记录及这些状态字段，保留现有存储行为。

最终离线测试全绿，`git diff --check` 通过。并发用例在复跑时暴露首次启用 WAL 的锁竞争，已设置 busy_timeout 并仅对 SQLITE_BUSY/SQLITE_LOCKED 做有时限重试；其他数据库错误继续抛出。旧库四进程迁移使用三个独立数据库重复验证，并额外复跑。

固定四条样本使用真实 scraper 循环、dispatcher、SellerProfileCache 和临时 SQLite；浏览器、卖家实际抓取、图片、AI、通知为离线替身，保留原 Prompt 与图片选项断言：

| 模式 | 详情创建/导航 | 画像加载/缓存 miss | 图片入口 | AI 入口 | 结果保存/通知 | 数据库快照 |
| --- | --- | --- | --- | --- | --- | --- |
| off | 各 4 | 各 4 | 4 | 4 | 各 4 | 4 |
| shadow | 各 4 | 各 4 | 4 | 4 | 各 4 | 4 |
| enforce | 各 3 | 各 3 | 3 | 3 | 各 3 | 4 |

删除的仅第一条旧代商品的整个下游调用集合。shadow 为 1 次 would_reject，enforce 为 1 次 skipped。另有共用卖家对照：加载入口分别 4/4/3 次，但实际缓存 miss 均为 1 次；因此不能按 skipped 的固定倍数宣称节省真实请求。

额外覆盖：M1–M5/Intel、全角/紧凑写法、目标优先、多个型号、规格、配件/比较/否定、字面量元字符、未知/超长/截断/非字符串标题、错误放行、任务隔离、旧库四进程并发迁移（3 个独立数据库）且保留任务/结果/价格数据、重复 bootstrap、损坏规则、JSON/SQLite 往返、API 422 无半写入、只改预筛不生成 Criteria、异步 AI/关键词生成、CLI 两种配置到实际 scraper、历史去重先行、全拒后翻页、重复拒绝不缓存且修正标题可进入、debug limit、off 恢复、关键词模式独立性、原爬虫错误重试与失败/取消汇总。

独立回放命令：

```bash
/workspace/.venv/bin/python -m tools.replay_listing_prefilter \
  --policy examples/mac-mini-prefilter.patch.json --input examples/prefilter-titles.json
```

输出 4 次出现、1 REJECT、3 KEEP、1 个唯一拒绝链接；这是规则判定，不是线上请求节省测量。

## 启用及回滚

详见 README 的“列表标题预筛”。迁移随启动执行；历史任务缺失字段即 off。通过已有部署访问控制 PATCH `examples/mac-mini-prefilter.patch.json` 到目标任务并 GET 核对，先保持 shadow；正常调度下至少观察两个周期并复核样本后才考虑整对象切换 enforce。坏配置和本地评估异常放行。

回滚优先 PATCH `{"prefilter":null}`，或完整对象改为 `mode=off`；运行中任务仍持旧快照，立即生效须按现有管理流程停止后重启。保留新增数据库列、不重建表、不清空原去重。代码回滚前导出规则并做一致备份，旧版保存可能把新列重置为 NULL。

未验证：真实账号登录、搜索流量、模型请求、真实耗时/节省量、线上 shadow 观察和 enforce 切换。未改变任何线上模式，也未执行前端构建（本次没有前端源代码改动）。
