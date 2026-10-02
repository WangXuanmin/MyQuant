# Spec：第一优先级回测指标组件

> 状态：Draft v0.1  
> 所属项目：`q10-trading-assistant`  
> 参考：Q5 的基准与风险调整指标、收益分布与回撤分析 spec  
> 本 spec 只定义指标组件及其验收，不负责修改策略逻辑。

## 上下文

当前 `open-xquant` 的 `oxq.portfolio.analytics.RunResult` 已提供：

- `total_return()`；
- `annualized_return()`，实际公式为 CAGR；
- `annualized_volatility()`；
- `max_drawdown()`；
- `sharpe_ratio()`；
- `calmar_ratio()`；
- `sortino_ratio()`；
- `daily_returns()`、`monthly_returns()`、`drawdown_series()`；
- `weights_df()`、`adj_weights_df()`、`positions_df()`。

第一优先级还需要统一封装：CAGR 别名、下行波动率、回撤事件与时长、最差日/月/年、历史 CVaR、换手率、费用与成本侵蚀、平均资金利用率、现金比例和现金拖累。

这些指标不得继续以 Notebook 临时函数存在。它们需要进入可测试的 Python 包，并可以被回测、每日交易报告和未来 UI 使用同一套接口调用。

## 任务

在 `q10-trading-assistant` 中实现一个类型明确、可直接调用、可批量导出的回测指标组件。组件接受现有 `RunResult`，不修改或 monkey-patch 已安装的 `open-xquant`。

推荐调用方式：

```python
from xquant_assistant.metrics import BacktestMetrics, MetricConfig

metrics = BacktestMetrics.from_run_result(
    result,
    config=MetricConfig(),
    benchmark_returns=benchmark_returns,
    gross_result=gross_result,
)

metrics.cagr()
metrics.downside_volatility()
metrics.sortino_ratio()
metrics.calmar_ratio()
metrics.max_drawdown_duration()
metrics.longest_underwater_days()
metrics.worst_period_returns()
metrics.historical_cvar()
metrics.turnover()
metrics.cost_drag()
metrics.average_exposure()
metrics.cash_drag()
metrics.summary()
```

## 1. 包结构

```text
src/xquant_assistant/metrics/
  __init__.py
  config.py
  errors.py
  models.py
  normalization.py
  returns.py
  drawdown.py
  tail_risk.py
  turnover.py
  costs.py
  exposure.py
  facade.py
tests/metrics/
  test_normalization.py
  test_returns.py
  test_drawdown.py
  test_tail_risk.py
  test_turnover.py
  test_costs.py
  test_exposure.py
  test_facade.py
```

`xquant_assistant.metrics.__init__` 至少导出：

```python
BacktestMetrics
MetricConfig
MetricReport
DrawdownEvent
DrawdownSummary
WorstPeriodReturn
TurnoverResult
CostDragResult
ExposureSummary
CashDragResult
MetricInputError
MetricConfigError
```

## 2. 配置对象

```python
@dataclass(frozen=True)
class MetricConfig:
    periods_per_year: int = 252
    risk_free_rate: float = 0.0
    minimum_acceptable_return: float = 0.0
    drawdown_threshold: float = -0.001
    cvar_confidence: float = 0.95
    cash_annual_return: float = 0.0
    include_initial_turnover: bool = False
```

约束：

- `periods_per_year` 必须是正整数；
- 年化收益率参数使用小数，例如 `0.02` 表示 2%；
- `drawdown_threshold` 必须位于 `[-1, 0)`；
- `cvar_confidence` 必须位于 `(0, 1)`；
- 配置不可变，必须写入每次运行的 `run_manifest.json`。

## 3. 输入标准化

实现内部函数：

```python
def equity_series_from_result(result: RunResult) -> pd.Series:
    ...

def normalize_return_series(returns: pd.Series) -> pd.Series:
    ...
```

### 3.1 权益曲线规则

- 从 `result.equity_curve` 构造 `DatetimeIndex`、`float64` Series；
- 日期必须唯一且严格递增；
- 权益值必须有限且严格大于 0；
- 不得自动排序后掩盖重复日期；
- 不得自动填充缺失权益；
- 少于 2 个权益点时，依赖收益的指标返回 `np.nan` 并在报告中记录 `insufficient_data`；
- 无效输入抛出 `MetricInputError`，不得返回看似正常的 0。

### 3.2 收益率口径

- 日收益默认使用简单收益：`equity.pct_change()`；
- CAGR 使用权益起止值和交易期数；
- 下行波动率和 Sortino 为兼容现有 `RunResult.sortino_ratio()`，使用对数收益；
- 序列索引、时区和名称在计算过程中保留；
- `NaN`、`Inf` 不得静默转为 0。

## 4. CAGR

### 4.1 API

```python
def cagr(self) -> float:
    ...
```

### 4.2 公式

```text
n = 权益点数量 - 1
CAGR = (V_end / V_start) ** (periods_per_year / n) - 1
```

### 4.3 行为

- 结果必须与 `RunResult.annualized_return(periods_per_year)` 在 `1e-12` 容差内一致；
- 少于 2 个权益点返回 `np.nan`；
- `V_start <= 0` 或任意权益非有限值抛出 `MetricInputError`；
- `summary()` 的字段名为 `cagr`，不再同时输出含义相同的第二个“年化收益率”字段。

## 5. 下行波动率

### 5.1 API

```python
def downside_volatility(self) -> float:
    ...
```

### 5.2 公式

为兼容当前 oxq Sortino 口径：

```text
r_t = log(V_t / V_t-1)
MAR_daily = log(1 + minimum_acceptable_return) / periods_per_year
d_t = r_t - MAR_daily，仅保留 d_t < 0 的观测
DownsideVol = sqrt(mean(d_t ** 2)) * sqrt(periods_per_year)
```

### 5.3 行为

- 没有低于最低可接受收益的观测时返回 `np.nan`；
- `minimum_acceptable_return <= -1` 时抛出配置错误；
- 结果为非负数；
- 不使用普通标准差替代下行均方根。

## 6. Sortino Ratio

### 6.1 API

```python
def sortino_ratio(self) -> float:
    ...
```

### 6.2 公式

默认保持现有 oxq 语义：

```text
AnnualizedLogReturn = mean(log_return) * periods_per_year
Sortino = (AnnualizedLogReturn - risk_free_rate) / DownsideVol
```

### 6.3 行为

- 默认配置下，存在负收益样本时应与 `RunResult.sortino_ratio()` 在 `1e-12` 容差内一致；
- 下行波动率为 0 或 `np.nan` 时返回 `np.nan`；
- 不返回 `inf`；
- `risk_free_rate` 解释为年化连续比较基准，与现有 oxq 接口保持一致。

## 7. Calmar Ratio

### 7.1 API

```python
def calmar_ratio(self) -> float:
    ...
```

### 7.2 公式

```text
Calmar = CAGR / abs(MaxDrawdown)
```

### 7.3 行为

- 最大回撤小于 0 时，应与 `RunResult.calmar_ratio()` 在 `1e-12` 容差内一致；
- 最大回撤为 0 时返回 `np.nan`，而不是 `0` 或 `inf`；
- 允许 CAGR 为负，此时 Calmar 为负。

## 8. 回撤事件与持续时间

### 8.1 数据模型

```python
@dataclass(frozen=True)
class DrawdownEvent:
    peak_date: pd.Timestamp
    start_date: pd.Timestamp
    trough_date: pd.Timestamp
    recovery_date: pd.Timestamp | None
    depth: float
    decline_sessions: int
    recovery_sessions: int | None
    total_sessions: int
    recovered: bool

@dataclass(frozen=True)
class DrawdownSummary:
    max_drawdown: float
    max_drawdown_duration: int
    longest_underwater_days: int
    longest_recovery_sessions: int | None
    unrecovered: bool
    event_count: int
```

### 8.2 API

```python
def drawdown_events(self) -> tuple[DrawdownEvent, ...]:
    ...

def drawdown_summary(self) -> DrawdownSummary:
    ...

def max_drawdown_duration(self) -> int | None:
    ...

def longest_underwater_days(self) -> int | None:
    ...
```

### 8.3 事件识别

- 使用 `result.drawdown_series()` 等价公式：`equity / equity.cummax() - 1`；
- `peak_date` 是跌破阈值前最近一次达到当前历史高点的日期；
- `start_date` 是回撤首次严格低于 `drawdown_threshold` 的日期；
- `trough_date` 是该事件回撤最深的第一个日期；
- `recovery_date` 是回撤首次恢复到 `drawdown_threshold` 及以上的日期；
- 样本结束仍未恢复时，`recovery_date=None`、`recovered=False`；
- 新高与阈值之间的微小数值波动不应被拆成大量假事件；
- `depth` 为负数；
- session 数使用索引位置差，不使用自然日差。

### 8.4 两个持续时间指标的区别

- `max_drawdown_duration`：**发生最大回撤深度的那个事件**从 `start_date` 到恢复日或样本末日的 session 数；
- `longest_underwater_days`：所有回撤事件中 `total_sessions` 的最大值，不一定属于最深事件。

该命名必须在文档和报告中保持一致，不得把二者混用。

## 9. 最差日、月、年

### 9.1 数据模型和 API

```python
@dataclass(frozen=True)
class WorstPeriodReturn:
    frequency: str
    label: str
    start: pd.Timestamp
    end: pd.Timestamp
    value: float
    partial_period: bool

def worst_period_returns(self) -> dict[str, WorstPeriodReturn | None]:
    ...
```

返回键固定为：

```python
{"day": ..., "month": ..., "year": ...}
```

### 9.2 计算规则

- 日：使用简单日收益；
- 月：使用每月最后一个权益点，相对上月最后一个权益点计算；
- 年：使用每年最后一个权益点，相对上年最后一个权益点计算；
- 首月和首年使用回测第一个权益值作为基准，与当前 `monthly_returns()` 语义一致；
- 首尾不完整月份/年份仍参与比较，但必须标记 `partial_period=True`；
- 收益相同则返回时间更早的一期；
- 没有足够观测时对应值为 `None`。

## 10. Historical CVaR / Expected Shortfall

### 10.1 API

```python
def historical_var(self, confidence: float | None = None) -> float:
    ...

def historical_cvar(self, confidence: float | None = None) -> float:
    ...
```

### 10.2 公式

使用简单日收益和历史模拟法：

```text
alpha = 1 - confidence
VaR_return = quantile(daily_returns, alpha, method="linear")
CVaR_return = mean(daily_returns[daily_returns <= VaR_return])
```

### 10.3 返回符号

- 返回的是**尾部收益率**，不是正数损失金额；
- 因此正常情况下 VaR 和 CVaR 为负，例如 `-0.035` 表示最差尾部平均日收益约为 `-3.5%`；
- 报表中文名使用“历史 CVaR（尾部收益）”，避免和正数损失口径混淆；
- 样本过少或尾部集合为空时返回 `np.nan`；
- `CVaR <= VaR` 应成立。

## 11. 换手率

“换手率”存在多种行业定义。组件必须同时提供目标权重换手率和实际成交金额换手率，不得只输出一个没有口径说明的数字。

### 11.1 数据模型

```python
@dataclass(frozen=True)
class TurnoverResult:
    weight_turnover_total: float | None
    weight_turnover_annualized: float | None
    traded_notional: float
    traded_notional_ratio: float | None
    traded_notional_ratio_annualized: float | None
    observation_sessions: int
    include_initial_allocation: bool
```

### 11.2 API

```python
def turnover(self) -> TurnoverResult:
    ...
```

### 11.3 权重换手率

优先使用 `result.adj_weights_df()`：

1. 补齐全部资产列；
2. 若没有 `CASH` 列，则 `CASH = 1 - 风险资产权重之和`；
3. 每期换手：`0.5 * sum(abs(w_t - w_t-1))`；
4. 默认不把初始从 100% 现金建仓计入换手；
5. `include_initial_turnover=True` 时，在首行前增加 `CASH=1` 的初始权重；
6. 总换手为各期换手之和；
7. 年化换手为 `总换手 * periods_per_year / observation_sessions`。

### 11.4 实际成交金额换手率

```text
traded_notional = sum(abs(fill.order.shares * fill.filled_price))
average_equity = mean(equity)
traded_notional_ratio = traded_notional / average_equity
annualized = traded_notional_ratio * periods_per_year / observation_sessions
```

该指标是双边总成交金额口径，买入和卖出都会计入；不再除以 2。报表必须显示名称“成交金额/平均净值”，不得与 `0.5 × 权重变化` 口径混淆。

## 12. 费用与成本侵蚀

### 12.1 数据模型

```python
@dataclass(frozen=True)
class CostDragResult:
    total_fees: float
    fee_ratio_initial_equity: float | None
    gross_total_return: float | None
    net_total_return: float
    return_drag: float | None
    terminal_value_drag: float | None
    path_dependent: bool
```

### 12.2 API

```python
def total_fees(self) -> float:
    ...

def cost_drag(self) -> CostDragResult:
    ...
```

### 12.3 费用

```text
total_fees = sum(float(fill.fee) for fill in result.trades)
fee_ratio_initial_equity = total_fees / initial_equity
```

`total_fees` 只表示显式费用，不把滑点和机会成本混入。

### 12.4 毛净收益侵蚀

准确计算 `gross return - net return` 需要同一策略、同一数据、同一信号与成交规则分别运行：

- `gross_result`：无手续费、无滑点；
- `net_result`：使用目标手续费和滑点。

```text
return_drag = gross_result.total_return() - net_result.total_return()
terminal_value_drag = gross_terminal_value - net_terminal_value
```

约束：

- 两条权益曲线必须具有相同起始日、结束日和初始权益；
- 若费用导致下单数量或后续交易路径不同，`path_dependent=True`；
- 不得把 `total_fees / initial_equity` 冒充完整成本侵蚀；
- 没有 `gross_result` 时仍返回费用字段，但 `return_drag` 和 `terminal_value_drag` 为 `None`。

## 13. 平均资金利用率和现金比例

### 13.1 数据模型和 API

```python
@dataclass(frozen=True)
class ExposureSummary:
    average_exposure: float | None
    minimum_exposure: float | None
    maximum_exposure: float | None
    average_cash_ratio: float | None
    maximum_cash_ratio: float | None
    sessions: int

def exposure_series(self) -> pd.Series:
    ...

def cash_ratio_series(self) -> pd.Series:
    ...

def average_exposure(self) -> float:
    ...

def exposure_summary(self) -> ExposureSummary:
    ...
```

### 13.2 公式

MVP 只支持无杠杆、只做多组合，使用 `BarSnapshot`：

```text
cash_ratio_t = snapshot.cash / snapshot.total_value
exposure_t = 1 - cash_ratio_t
average_exposure = mean(exposure_t)
```

### 13.3 行为

- `snapshot.total_value <= 0` 时对应日期为无效输入；
- 不裁剪负现金或大于 100% 的暴露，因为裁剪会掩盖异常；
- 出现 `cash_ratio < 0` 或 `exposure > 1` 时报告 `leverage_or_negative_cash`；
- 没有 snapshots 时返回空 Series 和 `None`，不得用目标权重冒充实际资金利用率；
- `average_cash_ratio` 与 `average_exposure` 在有效长仓无杠杆样本中之和应为 1。

## 14. 现金拖累

现金拖累无法仅凭策略权益曲线唯一确定，因为需要说明“现金本来应该投资到哪里”。因此该指标明确为相对指定基准的机会成本估计。

### 14.1 数据模型和 API

```python
@dataclass(frozen=True)
class CashDragResult:
    benchmark_name: str
    actual_total_return: float
    counterfactual_total_return: float
    cash_drag: float
    average_cash_ratio: float
    aligned_sessions: int

def cash_drag(
    self,
    benchmark_returns: pd.Series | None = None,
    benchmark_name: str = "benchmark",
) -> CashDragResult | None:
    ...
```

### 14.2 估计方法

使用上一交易日现金比例，避免未来信息：

```text
cash_daily_return = (1 + cash_annual_return) ** (1 / periods_per_year) - 1
incremental_return_t = cash_weight_t-1 * (benchmark_return_t - cash_daily_return)
counterfactual_return_t = actual_return_t + incremental_return_t

actual_total_return = product(1 + actual_return_t) - 1
counterfactual_total_return = product(1 + counterfactual_return_t) - 1
cash_drag = counterfactual_total_return - actual_total_return
```

### 14.3 解释

- `cash_drag > 0`：如果现金按基准投资，组合可能获得更高收益，现金形成机会成本；
- `cash_drag < 0`：基准下跌时现金起到了保护作用；
- 这是基于指定基准的反事实估计，不是确定的会计归因；
- 基准收益和现金比例必须按日期内连接；
- 使用 `cash_weight.shift(1)`；
- 对齐后不足 2 个收益日时返回 `None`；
- 报告必须显示 `benchmark_name`。

## 15. 批量报告接口

### 15.1 数据模型

```python
@dataclass(frozen=True)
class MetricReport:
    metrics: dict[str, float | int | str | bool | None]
    warnings: tuple[str, ...]
    config: MetricConfig
```

### 15.2 API

```python
def summary(self) -> MetricReport:
    ...

def to_frame(self) -> pd.DataFrame:
    ...

def to_json_dict(self) -> dict:
    ...
```

`summary()` 至少包含：

```text
total_return
cagr
annualized_volatility
downside_volatility
sharpe_ratio
sortino_ratio
calmar_ratio
max_drawdown
max_drawdown_duration
longest_underwater_days
worst_day
worst_month
worst_year
historical_var_95
historical_cvar_95
weight_turnover_total
weight_turnover_annualized
traded_notional_ratio
traded_notional_ratio_annualized
total_fees
fee_ratio_initial_equity
return_drag
average_exposure
average_cash_ratio
cash_drag
```

### 15.3 序列化

- `np.nan`、`pd.NaT` 和无定义指标在 JSON 中序列化为 `null`；
- 日期使用 ISO 8601；
- 百分比保持小数，不在数据层转换成带 `%` 的字符串；
- 人类可读格式只在 HTML/Notebook 展示层处理；
- `warnings` 必须包含缺少 snapshots、缺少 gross result、缺少 benchmark、样本不足和路径依赖等情况。

## 16. 与现有 RunResult 的兼容

- 不修改已安装 site-packages；
- `BacktestMetrics.from_run_result(result)` 保存对原始结果的只读引用；
- CAGR、Sortino、Calmar、最大回撤等应优先使用统一内部实现，并在测试中与现有 `RunResult` 对照；
- 对于现有 `RunResult` 把未定义值返回 `0` 的情况，新组件使用 `np.nan/None`，避免把“无数据”误解为“风险为零”；
- 后续若 open-xquant 正式加入同名指标，适配层必须检测版本并避免重复计算口径漂移。

## 17. 测试要求

### 17.1 单元测试原则

- 不使用网络；
- 不依赖动态日期；
- 使用固定人工权益曲线、快照和成交；
- 每个公式至少有一个手算可验证案例；
- 浮点数使用明确容差；
- 测试正常、空数据、单点、全上涨、全下跌、平盘、未恢复回撤、缺失 snapshots 和无 gross result。

### 17.2 必测案例

#### Case A：稳定上涨

```text
equity = [100, 101, 102, 103]
```

验证：

- CAGR 为正；
- 最大回撤为 0；
- 下行波动率、Sortino 和 Calmar 为 `np.nan`；
- 回撤事件为 0；
- 最差日仍为正收益。

#### Case B：已恢复回撤

```text
equity = [100, 110, 88, 92, 110, 112]
```

验证：

- 最大回撤为 `-20%`；
- 峰值、谷底和恢复日期正确；
- `recovered=True`；
- decline、recovery、total sessions 使用索引位置差。

#### Case C：未恢复回撤

```text
equity = [100, 120, 90, 95]
```

验证：

- `recovery_date=None`；
- `unrecovered=True`；
- 事件持续到样本最后一个 session。

#### Case D：权重换手

```text
t0: CASH=1.0
t1: A=0.5, B=0.5, CASH=0.0
t2: A=0.2, B=0.8, CASH=0.0
```

验证：

- 不含初始建仓时，权重换手总和为 `0.3`；
- 含初始建仓时，权重换手总和为 `1.3`。

#### Case E：费用与毛净结果

- 使用相同日期、相同初始权益的 gross/net 结果；
- 验证 `return_drag = gross_return - net_return`；
- 验证 `total_fees` 等于 fills 中 fee 之和；
- 交易数量或最终持仓不同时 `path_dependent=True`。

#### Case F：资金利用率

```text
total_value = [100, 100, 100]
cash = [100, 40, 10]
```

验证：

- exposure 为 `[0.0, 0.6, 0.9]`；
- average exposure 为 `0.5`；
- average cash ratio 为 `0.5`。

#### Case G：现金拖累

- 固定现金比例、实际收益和基准收益；
- 验证使用上一日现金比例；
- 基准上涨时 cash drag 为正；
- 基准下跌时允许 cash drag 为负。

### 17.3 回归测试

选择现有 Q3/Q5 固定回测结果，验证：

- `cagr()` 与 `result.annualized_return()` 一致；
- 有下行样本时 `sortino_ratio()` 与现有实现一致；
- 最大回撤非零时 `calmar_ratio()` 与现有实现一致；
- 最大回撤和 `drawdown_series().min()` 一致；
- 保存一份不含格式化字符串的 JSON golden fixture；
- 不使用课程旧 spec 中可能因数据源变化而失效的固定收益数字作为公式测试。

## 18. 结果呈现

实现完成后至少提供：

1. 所有第一优先级指标的直接调用示例；
2. 单策略指标总表；
3. 多策略横向对比 DataFrame；
4. 回撤事件明细；
5. 毛净成本侵蚀明细；
6. 权重换手与成交金额换手的双口径说明；
7. 资金利用率和现金拖累说明；
8. JSON 序列化示例；
9. 单元测试和现有 RunResult 回归测试结果。

## 19. 验收标准

1. 所有指标位于 `xquant_assistant.metrics` 包内，Notebook 不含重复公式实现。
2. `BacktestMetrics.from_run_result(result)` 可以直接使用。
3. 第一优先级指标均有独立方法、类型标注和 docstring。
4. `summary()` 可以一次返回全部核心指标。
5. 现有 CAGR、Sortino、Calmar 和最大回撤在有效样本上与 RunResult 一致。
6. 未定义指标返回 `np.nan/None`，不得静默返回 0。
7. 回撤事件可以正确处理已恢复和未恢复状态。
8. 最大回撤持续时间和最长水下时间语义明确且分别计算。
9. CVaR 使用历史简单收益，返回负的尾部收益口径。
10. 换手率同时提供权重变化和实际成交金额两种口径。
11. 成本侵蚀在缺少 gross result 时不伪造结果。
12. 平均资金利用率使用实际 snapshots，不使用目标权重近似。
13. 现金拖累明确依赖指定基准并使用滞后现金比例。
14. 所有指标可以写入 JSON，非法浮点转换为 `null`。
15. 全部单元测试和固定回归测试通过。

## 20. 待确认口径

以下问题不影响文档完成，但在实现前需要用户确认。本文已经给出默认选择：

1. **Sortino 下行波动率**  
   默认：保持当前 open-xquant 口径，只对低于 MAR 的收益计算均方根；另一种常见口径会把所有非下行日期记为 0 后再除以全部样本数，数值会不同。

2. **回撤恢复阈值**  
   默认：沿用 Q5 spec 的 `-0.1%`，避免浮点噪声生成假事件。严格恢复到历史高点时可改为 0。

3. **成本侵蚀**  
   默认：要求同策略分别运行无成本和有成本版本。仅凭一条净值曲线无法准确还原完整成本侵蚀。

4. **现金拖累基准**  
   默认：ETF sleeve 使用对应买入持有基准，A 股 sleeve 使用选定的宽基指数。不同基准会产生不同现金机会成本。

5. **回撤时间单位**  
   默认：使用交易 session 数。日报可以额外显示自然日，但验收和策略比较使用 session 数。
