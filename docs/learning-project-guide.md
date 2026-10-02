> 原项目开发说明归档：路径和示例日期需要按本机调整；首次安装以根目录 README 为准。

# q10 Trading Assistant MVP

这是一个本地运行的量化交易辅助工具 MVP。它使用新浪原始日线完成境内 ETF
和 A 股主板的每日 Universe/交易状态、量价与可选基本面/行业排名、50%/50% sleeve、目标仓位、交易前风控、
订单草稿、本地下一交易日开盘模拟成交、账户对账和报告输出。

系统没有券商或交易终端适配器，所有订单与成交均为本地假设记录。

## 当前数据口径

- 行情主源：新浪，通过 AkShare。
- ETF 日线：`fund_etf_hist_sina`。
- A 股日线：`stock_zh_a_daily(adjust="")`。
- 价格口径：原始价格，不宣称前复权或总收益。
- 证券列表与收盘交易状态：每日保存新浪证券列表及交易所上市/退市参考数据；收盘推断不能替代开盘状态。开盘限制使用提前归档的 TQ 免费 `get_more_info` 实际行情日期、停牌标识和涨跌停价格，`get_stock_info` 重新检查 ST、退市与名称。状态未知时不放行相关买入。
- 行业分类：免费新浪行业，逐日存档；行业景气分由 20/60 日相对趋势与成分股宽度组成。
- 基本面：TdxQuant 免费本地适配器，优先直接调用 `tqcenter.py`，HTTP 端点作为后备；按公告日读取收入/扣非利润增长、ROE、负债率、现金质量、归母净利润、经营现金流和审计意见。
- TdxQuant 需要先登录并启动支持 TQ 的通达信客户端，且在 `vipdoc/cw` 准备专业财务数据；服务不可用时公开降级，不阻断量价空跑。
- ETF 质量：接口仍为待实现项。
- 可选数据缺失时：报告显示 `DEGRADED_PRICE_VOLUME_ONLY`，有效权重公开变为
  100% 量价，不用零值代替缺失数据。

新浪当前证券列表没有带生效日期的历史板块字段。每日 dry-run 会保存当天快照，
但现阶段的历史研究回测只能使用显式 symbol 和当前 Universe，因此会标记
`RESEARCH_ONLY_CURRENT_UNIVERSE`，不能视为已经消除幸存者偏差的正式验证。

## 快速检查

项目虚拟环境已通过标准 `.pth` 文件注册 `src`，可以直接运行：

```powershell
$python = "<VENV_ROOT>\Scripts\python.exe"
$project = "<PROJECT_ROOT>"

& $python -m xquant_assistant.cli --project-root $project doctor
```

## TdxQuant 本机配置

通达信量化模拟终端安装在 `E:\tdx`。项目默认直接加载
`E:\tdx\PYPlugins\user\tqcenter.py`；`http://127.0.0.1:17709/` 仅作为后备。
官方专业财务包已解压到 `E:\tdx\vipdoc\cw`。运行基本面抓取前，应先登录并保持
通达信客户端主窗口运行；`doctor` 会检查接口文件和财务 `.dat` 文件，但不会主动登录客户端。

只读连通性探针：

```powershell
& $python "$project\scripts\probe_tdxquant.py" --tdx-root E:\tdx --financial
```

## 每日模拟空跑

第一次建议使用小范围显式列表检查完整链路：

```powershell
& $python -m xquant_assistant.cli --project-root $project run-daily `
  --symbols sh510300,sh510500,sh600000,sh600519,sz000001,sz000002
```

不传 `--symbols` 时构建完整境内 ETF 和 A 股主板 Universe，并逐只下载所需行情。
这会产生大量新浪请求，应在确认小范围运行正常后再执行。

当日 16:00 后运行会处理该交易日；节假日不能产生新信号或成交。前一日的 `READY` 草稿
在当天开盘事件重新检查，最多尝试 3 个交易日，按信号日固定股数全额模拟成交。
开盘限制缺失或不可核实的订单保留或到期失效。ETF 单边滑点为 5 bps，
A 股单边滑点为 10 bps。订单预估、模拟成交和研究回测统一使用
净佣万 0.85、免五的费率模型；A 股另计证管费、经手费、过户费，卖出另计
印花税；ETF 另计基金经手费。报告与指标均为费后口径。

离线复用缓存：

```powershell
& $python -m xquant_assistant.cli --project-root $project run-daily `
  --as-of 2026-09-28 `
  --symbols sh510300,sh600000 `
  --offline
```

查看账户与最近运行状态：

```powershell
& $python -m xquant_assistant.cli --project-root $project account
& $python -m xquant_assistant.cli --project-root $project status
```

## 研究回测

回测必须显式指定 symbol，并使用当日收盘信号、下一交易日开盘成交：

```powershell
& $python -m xquant_assistant.cli --project-root $project backtest `
  --start 2026-01-05 `
  --end 2026-09-28 `
  --symbols sh510300,sh510500,sh600000,sh600519,sz000001,sz000002 `
  --benchmark sh510300
```

主基准为 50% ETF 等权 sleeve 加 50% A 股主板等权 sleeve，按策略调仓频率重置。
输出包含权益曲线、复合基准、成交、回撤事件、完整指标 JSON、数据质量和限制声明。
仅有公告日期的财报从下一交易日进入信号；修订发布时间也单独截断。
新浪行业归属只会从首次采集日开始生效，不能反推此前归属。缺失实际历史开盘状态时，
回测保留候选与订单事件，相关订单零成交；此时空仓净值不能作为策略有效性的证据。

## 每日运行产物

每次运行创建不可变目录 `runs/{run_id}/`：

- `run_manifest.json`：日期、策略版本、配置与源码哈希、运行状态；
- `data_quality.json`：数据缺失、时点、历史长度和 OHLC 检查；
- `security_status.csv`：当日证券状态、上市/退市日、停牌与涨跌停状态；
- `universe.csv`、`excluded_instruments.csv`：纳入与排除原因；
- `factor_scores.parquet`、`candidate_ranking.csv`：原始因子、标准化值、有效权重和排名；
- `fundamental_scores.csv`：公告日截断后的财务字段、覆盖率、评分和硬过滤原因；
- `industry_classification.csv`、`industry_metrics.csv`：行业归属、趋势、宽度和行业分；
- `target_weights.csv`、`risk_report.json`、`orders_draft.csv`；
- `fills.csv`、`positions_after.json`、`cash_ledger.csv`；
- `reconciliation.json`、`performance_snapshot.json`；
- `evidence_cards.json`、`daily_report.html`。

账户事实保存在 `data/state/paper_ledger.sqlite3`，旧 JSON/CSV 仅保留历史，不能继续编辑为当前账户。
最新监控状态位于
`data/monitoring/status.json`。

## 回测指标组件

```python
from xquant_assistant.metrics import BacktestMetrics, MetricConfig

metrics = BacktestMetrics.from_run_result(
    result,
    config=MetricConfig(),
    benchmark_returns=benchmark_returns,
    gross_result=gross_result,
)

report = metrics.summary()
single_strategy = metrics.to_frame()
json_payload = metrics.to_json_dict()
```

指标覆盖 CAGR、波动率、Sharpe、Sortino、Calmar、回撤事件和时长、最差日/月/年、
历史 VaR/CVaR、双口径换手率、费用与毛净侵蚀、实际资金利用率和基准相对现金拖累。
无定义值在 JSON 中转换为 `null`。

## 配置

配置位于 `configs/`：

- `data.yaml`：新浪行情、每日证券状态、行业/基本面缓存，以及 `E:/tdx/PYPlugins/user/tqcenter.py` 直接接口与 HTTP 后备端点；
- `universe.yaml`：资产范围、板块排除、历史、价格和流动性；
- `strategy.yaml`：量价因子、组件权重、Top N 和调仓周期；
- `risk.yaml`：单标的、现金、流动性和整手限制；
- `account.yaml`：50 万元人民币、空仓、本地 dry-run、分品种费率和滑点。
- `runtime.yaml`：3 日有效期、回撤只提示、固定股数、全额成交、SQLite 和同日只重建报告。

## 每日可靠性与回放（spec-03）

首次合格信号建立 `d0` 锚点，此后每 10 个真实交易日调仓。每个 sleeve 新买只从前 10 名选择，
持仓可缓冲保留到前 15 名，最多 10 个标的。退出池的持仓仍保留行情管理与 ETF/A 股元数据。
按买入批次保存可卖日期；未核实 ETF 产品结算规则时明确采用保守 T+1。
15% 回撤仅提示，其他条件合格时继续自动模拟成交。缺价持仓保留，陈旧估值不进入正式回撤序列。

账本在同一个 SQLite 事务写入现金、持仓、订单、成交、事件与输入引用。重复运行同一天只返回原提交并重建报告。
同日 `--analysis-revision` 单独生成分析，不应用到账户；中断发生在提交后时用 `rebuild-report` 恢复。
跨日更换配置记录启用日期、版本和哈希，取消旧配置未成交意图，保留原调仓锚点，等待合格的新信号。
缺少连续交易日输入时报 `NEEDS_RECONCILIATION`，历史试验使用隔离研究账户。

```powershell
# 初始化真实沪深交易日历（已缓存则 doctor 可离线检查）
& $python -m xquant_assistant.cli --project-root $project refresh-calendar
# 仅用于尚未升级的旧空仓账户：备份、保留资金与历史、取消旧草稿；不会覆盖已有账本
& $python -m xquant_assistant.cli --project-root $project migrate-ledger --cancel-pending
# 交易日 09:15–09:30 读取 TQ，--symbols 包括下一开盘所需的全部持仓/待处理订单
& $python -m xquant_assistant.cli --project-root $project capture-opening-status --symbols sh510300,sh600000
# 交易日收盘后执行；不会在假日或通过回填历史日期改变日常账户
& $python -m xquant_assistant.cli --project-root $project run-daily --symbols sh510300,sh600000
# 已提交日期的报告可以随时重建
& $python -m xquant_assistant.cli --project-root $project rebuild-report --as-of 2026-10-08
& $python -m xquant_assistant.cli --project-root $project run-daily --as-of 2026-10-08 --analysis-revision --offline
```

`capture-opening-status` 为手动只读采集；无后台任务。TQ 日期未更新、在节假日或开盘后取得的记录仅进入
`data/status_probes/`，不倒填 `data/opening_status/`。原始样本有哈希，成功的开盘档案不能被再次覆盖。
TQ 的 `get_zdt_data` 是涨跌停统计，不能替代 `get_more_info` 的实际价格限制。

输入保存到 `data/snapshots/<sha256>/input.json`，包含使用的行情、状态、财务、行业、日历、配置、代码哈希和起始账户。
`validate-parity --inputs <各交易日 input.json> --output <差异.json>` 在独立测试账本逐日比较；
配置、代码版本、日历不匹配或快照哈希损坏时停止回放。账户金额精确到分，日期、股数和状态完全一致。
验收脚本 `scripts/accept_spec03.py --output <隔离输出目录>` 保存测试结果和 22 日工程样本对照。

代码与离线验收通过为 `ENGINEERING_READY`。`doctor` 单独统计真实当前日期提交的前向记录；测试时钟和历史回放不计入。
累计至少 12 个真实交易日，覆盖 d0/d1/d10/d11 后才可为 `FORWARD_VALIDATED`。这不代表策略盈利或可直接实盘。
历史 Universe、ST、行业版本和财报修订历史仍不完整，报告公开相应覆盖缺口；公告原文模块仍属后续范围。

## 测试

```powershell
& $python -m unittest discover `
  -s "$project\tests" `
  -t $project `
  -v
```

测试不访问网络，覆盖指标公式、输入异常、Universe、证券状态、量价/基本面/行业因子、
公告日时点约束、组合与行业集中度风控、分品种滑点、本地成交、完整日报产物以及研究回测。


## 本地网页操作台

双击 `scripts/Start-Web.cmd`，浏览器访问 <http://127.0.0.1:8765>。重复启动会打开已运行的本项目页面；启动不会执行交易任务。只在本机访问，不接券商。

终端启动（适合查看日志）：

```powershell
$env:PYTHONPATH = "$PWD\src"
& ..\.venv\Scripts\python.exe -m xquant_assistant.cli --project-root "$PWD" web --port 8765
```

网页提供账户概览、候选搜索与 K 线、组合与订单、研究回测、运行记录、策略配置六页。操作窗口会说明是否改变账户。每日模拟复用现有事务引擎；分析修订、重建报告、采集状态和研究回测各自沿用原有边界。节假日不可新结算，开盘状态须在交易日 09:15–09:30 采集。

先检查缓存日期；默认操作范围是当前 6 个缓存证券，输入框可修改。K 线来自新浪原始价格。旧版候选/回测明确标识，不会重新恢复取消的旧订单。完整历史数据不足时，研究报告保留限制说明。

配置可以校验、保存并自动备份。保存不会立即交易，下一次每日运行启用；其余费率、账户与执行政策不通过网页修改。任务会持久记录，服务重启后中断任务不会自动重试。

使用 `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Stop-Web.ps1` 停止启动器创建的服务。终端方式启动则按 Ctrl+C。不要在操作进行中停止服务，遇到中断先查看账本与任务记录。

设计与验收标准：`specs/spec-04-web-console.md`。验收结果：`runs/acceptance-web-20261002/acceptance.md`。浏览器测试使用可选 Playwright 测试依赖和本机 Chrome，不是网页运行依赖。
