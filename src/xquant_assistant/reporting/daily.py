"""Self-contained HTML report for one dry-run decision."""

from __future__ import annotations

from html import escape
from pathlib import Path

import pandas as pd

from ..domain import (
    AccountState,
    CandidateScore,
    DataQualityReport,
    OrderDraft,
    RiskReport,
    RunManifest,
    SimulatedFill,
    TargetWeight,
    json_value,
)


def _table(rows: list[dict]) -> str:
    if not rows:
        return "<p>无记录</p>"
    return pd.DataFrame(rows).to_html(index=False, escape=True, border=0)


def write_daily_report(
    path: str | Path,
    manifest: RunManifest,
    candidates: tuple[CandidateScore, ...],
    targets: tuple[TargetWeight, ...],
    orders: tuple[OrderDraft, ...],
    fills: tuple[SimulatedFill, ...],
    risk: RiskReport,
    data_quality: DataQualityReport,
    account: AccountState,
) -> Path:
    """Write a local HTML report with no remote assets."""

    candidate_rows = [
        {
            "symbol": item.symbol,
            "name": item.name,
            "asset_type": item.asset_type,
            "rank": item.rank,
            "score": item.total_score,
            "selected": item.selected,
            "factor_mode": item.factor_mode,
            "reasons": "; ".join(item.reasons),
        }
        for item in candidates
    ]
    issue_rows = [json_value(issue) for issue in data_quality.issues]
    risk_rows = [json_value(check) for check in risk.checks]
    html = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>量化辅助工具日报</title>
<style>
body{{font-family:Segoe UI,Microsoft YaHei,sans-serif;margin:32px;color:#1f2937}}
h1,h2{{color:#111827}} .badge{{display:inline-block;padding:4px 10px;border-radius:12px;background:#e5e7eb}}
table{{border-collapse:collapse;width:100%;margin:12px 0 28px}} th,td{{border-bottom:1px solid #ddd;padding:7px;text-align:left;font-size:13px}}
th{{background:#f3f4f6}} .note{{background:#fff7ed;border-left:4px solid #f59e0b;padding:12px}}
</style></head><body>
<h1>量化交易辅助工具日报</h1>
<p><span class="badge">{escape(manifest.status)}</span>　运行日期 {escape(manifest.as_of_date)}　运行 ID {escape(manifest.run_id)}</p>
<div class="note">本报告来自本地模拟空跑。订单和成交均不发送到券商；费用按运行清单记录的模型计算。</div>
<h2>账户</h2><p>现金：{account.cash:,.2f} {escape(account.currency)}；持仓数：{len(account.positions)}；已实现盈亏：{account.realized_pnl:,.2f}</p>
<h2>候选排名</h2>{_table(candidate_rows)}
<h2>目标权重</h2>{_table([json_value(item) for item in targets])}
<h2>订单草稿</h2>{_table([json_value(item) for item in orders])}
<h2>本次假设成交</h2>{_table([json_value(item) for item in fills])}
<h2>风险检查</h2>{_table(risk_rows)}
<h2>数据质量</h2>{_table(issue_rows)}
<h2>运行告警</h2><ul>{''.join(f'<li>{escape(item)}</li>' for item in manifest.warnings) or '<li>无</li>'}</ul>
</body></html>"""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(html, encoding="utf-8")
    return destination
