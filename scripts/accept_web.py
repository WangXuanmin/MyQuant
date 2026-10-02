"""Read-only production UI checks plus mutations only on a fixture server.

Requires optional test dependency playwright and locally installed Chrome.
This script never submits jobs or saves configurations to --url.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from urllib.request import urlopen


def read_state(url):
    with urlopen(url+'/api/state') as response:
        return json.load(response)


def identity(state):
    return {key:state[key] for key in ('account','ledger','pending','curve','events')}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:8765')
    parser.add_argument('--fixture-url',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--playwright-dir',type=Path)
    parser.add_argument('--browser',default=r'C:\Program Files\Google\Chrome\Application\chrome.exe')
    args=parser.parse_args()
    if args.playwright_dir:
        sys.path.insert(0,str(args.playwright_dir))
    from playwright.sync_api import sync_playwright, expect
    args.output.mkdir(parents=True,exist_ok=True)
    before=read_state(args.url)
    production_root=Path(before['project_root'])
    ledger_path=production_root/'data/state/paper_ledger.sqlite3'
    ledger_hash=hashlib.sha256(ledger_path.read_bytes()).hexdigest()
    fixture_state=read_state(args.fixture_url)
    assert fixture_state['project_root']!=before['project_root']
    assert 'xquant-web-engineering-' in fixture_state['project_root']
    errors=[]
    console_errors=[]
    response_errors=[]
    checks=[]
    def checked(label):
        checks.append(label)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path=args.browser)
        page=browser.new_page(viewport={'width':1440,'height':1000},device_scale_factor=1)
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.on('console',lambda message:console_errors.append(message.text) if message.type=='error' else None)
        page.on('response',lambda response:response_errors.append({'url':response.url,'status':response.status}) if response.status>=400 else None)
        page.goto(args.url,wait_until='networkidle')
        expect(page.locator('.stat-value').first).to_contain_text('500,000.00')
        expect(page.get_by_role('button',name='运行每日模拟',exact=True)).to_be_disabled()
        expect(page.locator('#equity-chart')).to_contain_text('暂无新版净值曲线')
        page.screenshot(path=str(args.output/'overview.png'),full_page=True)
        checked('真实账户现金、空仓、空净值与节假日按钮一致')
        page.get_by_role('link',name='候选与行情',exact=True).click()
        page.wait_for_load_state('networkidle')
        expect(page.locator('#candlestick-chart svg')).to_be_visible()
        expect(page.locator('#quote-source')).to_contain_text('2026-09-28')
        initial=page.locator('#candidate-table tbody tr').count()
        page.locator('#asset-filter').select_option('CN_ETF')
        assert 0<page.locator('#candidate-table tbody tr').count()<initial
        page.locator('#candidate-search').fill('510300')
        expect(page.locator('#candidate-table')).to_contain_text('SH510300')
        page.locator('#symbol-lookup').fill('sh510300')
        page.get_by_role('button',name='查看行情',exact=True).click()
        expect(page.locator('#quote-header')).to_contain_text('SH510300')
        page.screenshot(path=str(args.output/'candidates.png'),full_page=True)
        checked('候选过滤、名称代码搜索、真实缓存 K 线与均线')
        page.get_by_role('link',name='组合与订单',exact=True).click()
        expect(page.locator('main')).to_contain_text('当前空仓')
        assert page.locator('.timeline-item').count()>=5
        page.get_by_role('button',name='采集开盘状态',exact=True).click()
        expect(page.locator('#operation-impact')).to_contain_text('09:15–09:30')
        page.get_by_role('button',name='取消',exact=True).click()
        page.screenshot(path=str(args.output/'portfolio.png'),full_page=True)
        checked('真实账本、取消事件、开盘采集说明与取消操作')
        page.get_by_role('link',name='研究与回测',exact=True).click()
        expect(page.locator('#research-chart svg')).to_be_visible()
        expect(page.locator('#research-detail')).to_contain_text('-6.07%')
        expect(page.locator('#research-detail')).to_contain_text('旧版历史回测')
        with page.expect_download() as download_info:
            page.locator('a[download]').filter(has_text='metrics.json').click()
        download=download_info.value
        destination=args.output/'downloaded-metrics.json'
        download.save_as(str(destination))
        assert json.loads(destination.read_text(encoding='utf-8'))['metrics']['total_return']<0
        page.screenshot(path=str(args.output/'research.png'),full_page=True)
        checked('回测指标、净值、旧口径提示与 JSON 文件下载')
        page.get_by_role('link',name='运行记录',exact=True).click()
        page.get_by_role('button',name='查看报告 →').first.click()
        expect(page.locator('main')).to_contain_text('运行身份与数据状态')
        page.get_by_role('link',name='返回记录',exact=True).click()
        expect(page.locator('main')).to_contain_text('运行与研究报告')
        checked('历史报告查看与返回导航')
        page.get_by_role('link',name='策略配置',exact=True).click()
        page.locator('input[name="factor-momentum_20"]').fill('99')
        page.get_by_role('button',name='校验变更',exact=True).click()
        expect(page.locator('#config-result')).to_contain_text('合计 100%')
        page.locator('input[name="factor-momentum_20"]').fill('25')
        page.get_by_role('button',name='校验变更',exact=True).click()
        expect(page.locator('#config-result')).to_contain_text('配置校验通过')
        page.evaluate('scrollTo(0,0)')
        page.screenshot(path=str(args.output/'settings.png'),full_page=True)
        checked('真实配置只读校验与错误提示，不保存到真实项目')
        for width in (820,390):
            page.set_viewport_size({'width':width,'height':900})
            page.goto(args.url+'/#overview',wait_until='networkidle')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            page.screenshot(path=str(args.output/f'overview-{width}.png'),full_page=True)
        checked('820 / 390 像素响应布局无页面横向溢出')
        production_console_errors=list(console_errors)
        production_response_errors=list(response_errors)
        # All following mutation requests target an ephemeral fixture root.
        page.set_viewport_size({'width':1440,'height':1000})
        page.goto(args.fixture_url,wait_until='networkidle')
        page.get_by_role('button',name='运行每日模拟',exact=True).click()
        page.locator('input[name="offline"]').check()
        page.get_by_role('button',name='开始执行',exact=True).click()
        expect(page.locator('#task-content')).to_contain_text('操作完成',timeout=30000)
        assert read_state(args.fixture_url)['ledger']['revision']==1
        checked('隔离账户通过网页提交每日模拟并更新任务进度')
        page.get_by_role('button',name='关闭任务面板',exact=True).click()
        page.get_by_role('button',name='运行每日模拟',exact=True).click()
        page.locator('input[name="offline"]').check()
        page.get_by_role('button',name='开始执行',exact=True).click()
        expect(page.locator('#task-content')).to_contain_text('操作完成',timeout=30000)
        assert read_state(args.fixture_url)['ledger']['revision']==1
        checked('隔离账户同日网页重跑不重复提交或成交')
        page.get_by_role('button',name='关闭任务面板',exact=True).click()
        page.get_by_role('link',name='策略配置',exact=True).click()
        fixture_before=identity(read_state(args.fixture_url))
        page.locator('input[name="frequency"]').fill('5')
        page.get_by_role('button',name='保存配置',exact=True).click()
        expect(page.locator('input[name="frequency"]')).to_have_value('5')
        expect(page.locator('#toast')).to_contain_text('配置已保存')
        current=read_state(args.fixture_url)
        assert current['config']['strategy']['rebalance_sessions']==5
        assert identity(current)==fixture_before
        checked('隔离账户网页保存参数，账户及历史保持不变')
        page.goto(args.fixture_url+'/#research',wait_until='networkidle')
        page.get_by_role('button',name='新建回测',exact=True).click()
        page.locator('input[name="start"]').fill('2026-09-21')
        page.locator('input[name="end"]').fill('2026-09-28')
        page.get_by_role('button',name='开始执行',exact=True).click()
        expect(page.locator('#task-content')).to_contain_text('操作完成',timeout=30000)
        assert identity(read_state(args.fixture_url))==fixture_before
        checked('隔离账户网页研究回测生成独立结果，不修改日常账户')
        browser.close()
    after=read_state(args.url)
    assert identity(before)==identity(after)
    assert before['config_hash']==after['config_hash']
    assert hashlib.sha256(ledger_path.read_bytes()).hexdigest()==ledger_hash
    assert not errors,errors
    # The deliberately invalid weight preview must return a controlled 400.
    assert production_response_errors==[{'url':args.url+'/api/config','status':400}],production_response_errors
    assert len(production_console_errors)==1 and '400' in production_console_errors[0],production_console_errors
    result={'status':'PASS','checks':checks,'browser':'headless Chrome / Playwright',
        'page_errors':errors,'unexpected_console_errors':[],
        'expected_validation_response':production_response_errors,
        'account_before':identity(before),'account_after':identity(after),
        'ledger_sha256_before_and_after':ledger_hash,'config_hash_before_and_after':before['config_hash'],
        'mutation_target':'ephemeral engineering fixture only'}
    (args.output/'browser-acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':'PASS','browser_checks':len(checks),'account_unchanged':True},ensure_ascii=False))


if __name__=='__main__':
    main()
