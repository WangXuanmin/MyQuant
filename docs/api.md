# 本地 HTTP 接口

默认地址 `http://127.0.0.1:8765`；只绑定本机，不提供公网或券商接口。

## 只读接口

| 请求 | 参数 | 返回 |
| --- | --- | --- |
| `GET /api/state` | 无 | 当前账本、日历、新鲜度、配置哈希、候选来源、报告、任务、本地操作令牌 |
| `GET /api/market` | `symbol=sh510300&limit=120` | 本地新浪原始 OHLC、量额、日期；没有缓存返回空序列 |
| `GET /api/report` | `id=<报告目录名称>` | 候选、订单、风险、指标、净值和可导出文件 |
| `GET /api/jobs` | 无 | 持久任务记录；中断任务不会自动重试 |
| `GET /artifact/<id>/<file>` | 已保存报告文件名 | JSON/CSV/Parquet 下载或沙箱 HTML |

只读请求不会初始化模拟账户或联网下载行情。找不到交易日历或数据时返回明确状态，不能据此生成交易。

## 提交任务

`POST /api/jobs`，JSON 请求，使用页面取得的 `X-Local-Token`；Host 与 Origin 必须符合本机服务。不允许异源写入。令牌是每次服务启动随机生成的本地防跨站令牌，不是 GitHub 或券商凭据。

| action | 输入字段 | 影响 |
| --- | --- | --- |
| `daily` | `symbols`，可选 `date`、`offline` | 按原引擎提交模拟账户和收盘信号；只处理合格真实交易日 |
| `analysis` | `symbols`、已提交日期 `date`、可选 `offline` | 生成独立分析修订，账户不变 |
| `rebuild` | 已提交日期 `date` | 从账本重建日报，不重新成交 |
| `capture` | `symbols` | 读取 TQ，合格的真实开盘观察进入档案；不交易 |
| `refresh_data` | `symbols`、可选 `offline` | 刷新最近已完成交易日的新浪缓存；不交易 |
| `refresh_calendar` | 无 | 刷新新浪交易日历；不交易 |
| `backtest` | `symbols`、`start`、`end`、可选 `benchmark`、`offline` | 独立研究账户，输出指标和覆盖边界 |

`symbols` 使用逗号分隔的 sh/sz 六位代码，1–100 个去重标的；日期格式 YYYY-MM-DD；`offline` 必须是 JSON 布尔值；基准须在本次标的列表中。

正常提交返回 HTTP 202 和任务编号。服务串行执行任务，忙碌时返回 409；任务状态为 QUEUED、RUNNING、SUCCEEDED、BLOCKED、FAILED 或重启后的 INTERRUPTED。业务受阻并不等于成交成功；查看任务结果与账本。

## 配置校验与保存

`POST /api/config`：`expected_hash` 使用最新 `/api/state` 的配置哈希，`changes` 为分组对象，`preview:true` 仅校验。

- `universe`：asset_types、min_price、min_median_turnover_20d、custom_include、custom_exclude。
- `strategy`：rebalance_sessions、new_buy_top_n、hold_buffer_top_n、price_volume_weights、component_weights。
- `risk`：max_single_stock_weight、max_single_etf_weight、max_industry_weight、min_cash_weight。

权重合计须为 1，比例字段采用 0–1 小数。受保护的账户、费用、数据源、执行政策与板块排除规则不能通过网页修改。旧配置哈希、执行中的任务会拒绝保存。成功保存会备份配置并生成策略版本，账户当时不变，下一次每日运行由原引擎处理配置启用及旧意图取消。

## 校验和错误

400：参数格式或取值不合法。403：非本地 Host、异源或缺少操作令牌。404：接口不存在。409：任务繁忙或配置冲突。500：非预期读取/执行异常。后台任务本身的错误写入任务记录，不会被伪装为有效交易。

JSON/CSV 导出限制在选定报告目录内；HTML 不执行脚本。开发和测试中不得绕过真实日期、交易状态或 SQLite 一致性检查。
