# MyQuant

本机运行的量化交易辅助工具：浏览器操作台、Python 接口、模拟账户、量价策略、回测和指标组件。

覆盖境内 ETF 与沪深 A 股主板，支持可变研究范围；量价为主，免费基本面和行业景气数据可选。网页包含账户概览、候选与 K 线、组合与订单、研究回测、运行记录、策略配置六页。

## 安装与启动（Windows）

需要 Python 3.12+ 与 Git。推荐 Python 3.12；测试版本写在 `requirements-tested.txt`。

```powershell
git clone https://github.com/WangXuanmin/MyQuant.git
cd MyQuant
py -3.12 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements-tested.txt
& .\.venv\Scripts\python.exe -m pip install --no-deps -e .
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Start-Web.ps1
```

随后访问 <http://127.0.0.1:8765>。安装完成后也可双击 `scripts/Start-Web.cmd`。端口占用时可以给 PowerShell 脚本传入 `-Port 8770`。脚本优先使用本项目 `.venv`，同时兼容原课程目录上一级 `.venv`。

启动器只运行本地网页服务，不自动执行交易任务。重复启动打开同一服务。停止服务：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Stop-Web.ps1
```

在任务完成后再停止服务。若进程中断，先查账本与任务记录，不自动重试中断任务。

## 第一次使用

仓库仅包含代码、非敏感策略配置、测试和说明，不包含原用户的行情缓存、模拟账本、成交记录、报告、客户端、账号或密码。克隆后从空工作区开始，不会导入原账户。

1. 保持服务运行，进入“运行记录”，点击“更新日历”，生成新浪交易日历缓存。
2. 在“账户概览”或“候选与行情”更新新浪行情。初次建议 `sh510300,sh600000` 小范围试运行；后续可配置自己的研究名单。
3. 如需验证开盘成交，在交易日 09:15–09:30 采集真实开盘状态。
4. 交易日 16:00 后运行每日模拟，收盘信号产生的固定股数订单由下一交易日的模拟结算检查。
5. 首次合格结算才初始化新的模拟账户。默认资金 500,000 元、起初空仓；ETF/A 股预算 1:1。

未下载数据时，页面显示空状态。日历未就绪、节假日或数据不合格时不产生新模拟结算。需要完整 Universe 时再扩大范围，网页单次显式证券列表最多 100 个。

## 模拟执行口径

- 净佣万 0.85，免五；A 股及 ETF 其他费用独立计提。
- ETF 单边滑点 5 bps；A 股 10 bps。
- 订单最多保留 3 个交易日，每日重新风控；信号日确定股数，采用全额成交模型。
- 组合回撤达到 15% 只提示风险，继续模拟成交；停牌、涨跌停、现金及可卖数量等规则照常执行。
- 同一交易日期不能重复提交成交；分析修订、报告重建及研究回测不改变日常模拟账户。
- 不接券商或真实账户，没有真实下单接口。

## 数据与通达信

行情使用 AkShare 的新浪原始日线，未做前复权/总收益处理；暂不处理分红、送股、拆股。行业使用免费新浪行业分类和趋势/宽度指标，历史版本从采集日开始积累。

基本面与开盘状态适配免费的 TdxQuant。自行安装并登录支持 TQ 的通达信客户端，专业财务包需要本机准备。在 `configs/data.yaml` 设置本机 `tdxquant_tqcenter_path`；文件中的 `E:/tdx/...` 是安装路径示例，不是必需目录。客户端和 SDK 不随仓库发布，也没有保存登录信息。基本面或行业缺失时如实降级；开盘交易限制未知的相关订单不能成交。

配置文件均为策略参数，不能填写密码。若后续接入需要凭据的数据服务，应使用环境变量或操作系统凭据存储，不提交 `.env` 或凭据文件。

## 常用命令

```powershell
$python = '.\.venv\Scripts\python.exe'
& $python -m xquant_assistant.cli --project-root "$PWD" doctor
& $python -m xquant_assistant.cli --project-root "$PWD" refresh-calendar
& $python -m xquant_assistant.cli --project-root "$PWD" run-daily --symbols sh510300,sh600000
& $python -m xquant_assistant.cli --project-root "$PWD" backtest --start 2026-01-05 --end 2026-09-28 --symbols sh510300,sh600000 --benchmark sh510300
& $python -m unittest discover -s tests -t . -v
```

历史研究必须检查历史 Universe、开盘状态、财报公告/修订和行业归属时点的覆盖。缺少真实历史开盘状态时，相应订单无法可靠成交；空仓或旧回测结果不等同于策略验证成功。

## 代码与文档

| 目录 | 内容 |
| --- | --- |
| `src/xquant_assistant/web/` | 本地 HTTP 接口、HTML/CSS/JavaScript 操作台 |
| `runtime/`、`execution/` | 共享交易引擎、交易日历、SQLite 事务账本、定价与费用 |
| `data/`、`universe/` | 源码内的数据适配、状态、财报、行业及 Universe 组件 |
| `factors/`、`strategies/`、`portfolio/` | 因子、选股、组合构造及风控 |
| `metrics/`、`research/`、`reporting/` | 封装指标、回测、基准、证据与报告 |
| `configs/` | 非敏感的默认策略与模拟参数 |
| `tests/`、`examples/` | 隔离账户验收与指标调用示例 |
| `specs/`、`docs/` | 实现 spec、接口说明及验证记录 |

上表除 `configs/tests/examples/specs/docs` 外，模块目录均位于 `src/xquant_assistant/`。运行生成的根目录 `data/` 与 `runs/` 均被 Git 排除，和源码中的 `src/xquant_assistant/data/` 不同。

- [网页设计 spec](specs/spec-04-web-console.md)
- [本地 HTTP 接口说明](docs/api.md)
- [开发说明归档](docs/learning-project-guide.md)
- [发布验证记录](docs/verification.md)

浏览器测试脚本 `scripts/accept_web.py` 是原工作区的历史场景验收，依赖原缓存/旧报告；首次克隆应运行单元及接口测试，或使用临时工程服务 `tests/web/serve_fixture.py` 检查界面，不复制个人账本来充当测试数据。
