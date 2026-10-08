#!/usr/bin/env python3
"""Gut dashboard QA: real fixture WebSocket + static export server, no trading backend.

Run after frontend/npm run build. Requires the already-installed Python aiohttp and
Playwright. Uses local Chrome with an isolated temporary profile. All servers,
browsers and the temporary mutation build are closed in finally blocks.
The --out option can verify a deployed image's extracted frontend/out (R16).
"""
from __future__ import annotations
import argparse
import asyncio
import copy
import json
import mimetypes
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
from pathlib import Path
from aiohttp import web
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / 'docs/gut_dashboard/screenshots'
FIXTURES = ROOT / 'docs/gut_dashboard/fixtures'
CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
URL = 'http://127.0.0.1:8005'
PASSED = 0
FAILURES = []


def check(ok, message):
    global PASSED
    if ok:
        PASSED += 1
        print('  ok   ' + message, flush=True)
    else:
        FAILURES.append(message)
        print('  FAIL ' + message, flush=True)


def old_frame(name):
    # Data fixtures are retained; no code is imported from any retired verifier.
    return json.loads((ROOT / 'docs/overnight_holds/fixtures' / (name+'.json')).read_text())


def clean(frame):
    frame = copy.deepcopy(frame)
    frame['ingestion'] = dict(stock='connected', news='connected', vix='connected')
    frame['account']['is_circuit_broken'] = False
    frame['account']['risk_drawdown'] = 39.14
    frame['account']['daily_starting_equity'] = frame['account']['equity'] + 39.14
    frame['persistence']['status'] = 'durable'
    frame['broker'].update(account_number='FIXTURE-PAPER', mismatch=False)
    frame['working_orders_count'] = 0
    for s in frame['strategies']:
        s['window']['trading_day'] = True
        s['window']['state'] = 'DONE_FOR_DAY'
        s['window']['blockers'] = []
        s['window']['next_change_at'] = None
        if s.get('orb'):
            s['orb'].update(init_error=None,errors=[],alerts=[],orphans=[],open_trades=[],step=None,rules='adaptive-v1.7.1 · deal rule on')
        if s.get('tri_engine'): s['tri_engine']['last_error'] = None
    frame['overnight']['state'].update(running=True,mode='live',init_error=None)
    frame['overnight']['unsold_after_0931'] = []
    frame['overnight']['state']['unsold_after_0931'] = []
    for r in frame['overnight']['rows']: r['needs_look'] = []
    for h in frame['overnight']['holds']: h['needs_look'] = []
    return frame


def set_positions(fr, positions):
    fr['all_positions'] = positions
    fr['positions_count'] = len(positions)
    fr['primary_position'] = positions[0] if positions else None
    fr['broker']['alpaca_positions'] = {p['symbol']:p['shares'] for p in positions}


def position(symbol='TSLA', strategy='tsla_asymmetric_dual', **overrides):
    return dict(symbol=symbol, strategy_id=strategy, side='SHORT', shares=107,
                entry_price=376.12, market_price=375.40, market_value=40167.8,
                unrealized_pnl=77.04, unrealized_pnl_pct=.19, stop_loss=379.7,
                take_profit_1=370.75, take_profit_2=368.96, fixed_protection=True,
                exit_due='2026-10-07T12:48:00-04:00', **overrides)


def fixtures():
    evening=clean(old_frame('evening'))
    # Shift existing data to the canvas date; preserve payload shape and provenance.
    evening=json.loads(json.dumps(evening).replace('2026-09-30','2026-10-07').replace('2026-10-01','2026-10-08'))
    evening['_fixed_now']='2026-10-07T21:03:00-04:00'
    evening['timestamp']='2000-01-01T00:00:00Z'  # freshness must use receive time
    evening['market_context']['market_status']='CLOSED'
    evening['overnight']['no_buy_until']='2026-10-07T15:49:30-04:00'
    set_positions(evening,evening['all_positions'])
    for p in evening['all_positions']:
        p['market_price']=p['entry_price'];p['unrealized_pnl']=0
    opened=clean(old_frame('day'))
    opened=json.loads(json.dumps(opened).replace('2026-09-30','2026-10-07').replace('2026-10-01','2026-10-08'))
    opened['_fixed_now']=opened['timestamp']='2026-10-07T09:52:00-04:00'
    opened['market_context']['market_status']='OPEN'
    opened['overnight']['no_buy_until']='2026-10-07T15:49:00-04:00'
    iren=copy.deepcopy(next(p for p in evening['all_positions'] if p['symbol']=='IREN'))
    iren.update(exit_due='2026-10-07T09:30:00-04:00',market_price=41.60,unrealized_pnl=-30.94)
    ih=copy.deepcopy(next(h for h in evening['overnight']['holds'] if h['symbol']=='IREN'))
    ih.update(sale_date='2026-10-07',buy_date='2026-10-06')
    opened['overnight']['holds']=[ih]
    opened['overnight']['unsold_after_0931']=['IREN']
    for r in opened['overnight']['rows']:
        if r['symbol']=='IREN':r.update(state='HELD',buy_date='2026-10-06',sale_date='2026-10-07')
    set_positions(opened,[position(),iren])
    for s in opened['strategies']: s['window']['state']='MANAGING' if s['id']=='tsla_asymmetric_dual' else 'CAN_TRADE'
    lock=copy.deepcopy(opened)
    lock['_fixed_now']=lock['timestamp']='2026-10-07T15:35:00-04:00'
    set_positions(lock,[])
    lock['overnight']['holds']=[];lock['overnight']['unsold_after_0931']=[]
    for r in lock['overnight']['rows']:r.update(state=None,buy_date=None,sale_date=None)
    for s in lock['strategies']:s['window']['state']='CAN_TRADE' if s['id']=='news_momentum' else 'DONE_FOR_DAY'
    sunday=copy.deepcopy(evening)
    sunday['_fixed_now']=sunday['timestamp']='2026-10-11T10:00:00-04:00'
    sunday['market_context']['market_status']='OPEN'
    for s in sunday['strategies']:s['window'].update(trading_day=False,state='MARKET_CLOSED')
    for p in sunday['all_positions']:p['exit_due']='2026-10-12T09:30:00-04:00'
    for h in sunday['overnight']['holds']:h.update(sale_date='2026-10-12',nights='weekend')
    mismatch=copy.deepcopy(lock)
    mismatch['broker'].update(mismatch=True,alpaca_positions={'AAPL':5})
    out={'no-frame':None,'evening':evening,'09-52':opened,'15-35':lock,'sunday-open':sunday,'broker-mismatch':mismatch}
    FIXTURES.mkdir(parents=True,exist_ok=True)
    for name,fr in out.items(): (FIXTURES/(name+'.json')).write_text(json.dumps(fr,indent=2)+'\n')
    return out


def ledger(today):
    # Includes a recovered aggregate day, an empty weekday and a duplicated trade.
    date='2026-10-07'
    items=[dict(trade_id='tsla-1',session_date=date,symbol='TSLA',strategy_id='tsla_asymmetric_dual',
                side='SHORT',status='CLOSED',opened_at=date+'T09:48:00-04:00',closed_at=date+'T12:48:00-04:00',
                quantity=107,avg_entry_price=376.12,avg_exit_price=376.485794,realized_pnl=-39.14,fees=0,exit_reason='time limit')]
    sessions=[]
    for day,pnl in [('2026-10-01',-1106.97),('2026-10-02',655.86),('2026-10-05',393.18)]:
        sessions.append(dict(session_date=day,opening_equity=49000,closing_equity=49000+pnl,account_change=pnl,
          realized_pnl=pnl,finished_trade_result=pnl,trades_count=8,source='recovered_aggregate',aggregate_only=True,
          trade_detail_complete=False,fees=0,strategies={'overnight_nvda':{'realized_pnl':pnl,'trades_count':8}}))
    if today: sessions=[]
    total=round(sum(s['realized_pnl'] for s in sessions)-39.14,2)
    return dict(as_of=date+'T21:03:00-04:00',timezone='America/New_York',persistence={'status':'durable'},
      items=items+items,sessions=sessions,recovered_sessions=sessions,next_cursor=None,
      summary=dict(opening_equity=49000,current_equity=48960.86,realized_pnl=total,fees=0,fees_known=True,
                   trades_count=1+sum(s['trades_count'] for s in sessions),wins=14 if sessions else 0,losses=11 if sessions else 1,win_rate=.56))


class FixtureServer:
    def __init__(self,out):
        self.out=Path(out);self.current=None;self.clients=set();self.actions=[];self.posts=[]
        self.reply_status=200;self.reply=None;self.ledger_error=False
        self.loop=asyncio.new_event_loop();self.thread=threading.Thread(target=self.loop.run_forever,daemon=True)
    async def start_async(self):
        app=web.Application();app.router.add_route('*','/{tail:.*}',self.handle)
        self.runner=web.AppRunner(app,shutdown_timeout=1);await self.runner.setup()
        try: await web.TCPSite(self.runner,'127.0.0.1',8005).start()
        except BaseException: await self.runner.cleanup();raise
    def __enter__(self):
        self.thread.start()
        try:self.call(self.start_async())
        except BaseException:self.loop.call_soon_threadsafe(self.loop.stop);self.thread.join();self.loop.close();raise
        return self
    def call(self,coro):return asyncio.run_coroutine_threadsafe(coro,self.loop).result(timeout=15)
    async def broadcast(self):
        if self.current:
            for ws in list(self.clients):
                if not ws.closed:await ws.send_json(self.current)
    def show(self,fr):self.current=copy.deepcopy(fr);self.call(self.broadcast())
    async def stop_async(self):
        for ws in list(self.clients):await ws.close()
        await self.runner.cleanup()
    def __exit__(self,*_):
        try:self.call(self.stop_async())
        finally:self.loop.call_soon_threadsafe(self.loop.stop);self.thread.join();self.loop.close()
    async def handle(self,r):
        if r.path=='/ws/ui':
            ws=web.WebSocketResponse();await ws.prepare(r);self.clients.add(ws)
            if self.current:await ws.send_json(self.current)
            try:
                async for m in ws:
                    if m.type==web.WSMsgType.TEXT:self.actions.append(json.loads(m.data))
            finally:self.clients.discard(ws)
            return ws
        if r.path=='/health':return web.json_response({'limits':{'max_daily_loss_dollars':1243,'base_trade_risk_pct':.01},'research':{'written':483,'errors':0,'pending':0}})
        if r.path=='/api/trades':
            if self.ledger_error and r.query.get('range')=='7d':return web.json_response({'detail':'fixture failure'},status=503)
            return web.json_response(ledger(r.query.get('range')=='today'))
        if r.path=='/api/positions':return web.json_response({p['symbol']:p for p in (self.current or {}).get('all_positions',[])})
        if r.path=='/api/overnight/no-buy-tonight' and r.method=='POST':
            data=await r.json();self.posts.append(data)
            if self.reply_status!=200:return web.json_response({'detail':self.reply},status=self.reply_status)
            self.current['overnight']['no_buy_tonight']=data['on']
            await self.broadcast()
            return web.json_response({'ok':True,'message':REPLY_ON if data['on'] else REPLY_OFF})
        if r.path=='/api/account':return web.json_response((self.current or {}).get('account',{})) if self.current else web.Response(status=503)
        if r.path=='/api/strategies':return web.json_response((self.current or {}).get('strategies',[]))
        if r.path.startswith('/api/'):return web.json_response({})
        file=(self.out / (r.path.lstrip('/') or 'index.html')).resolve()
        if self.out.resolve() not in file.parents or not file.is_file():return web.Response(status=404)
        return web.FileResponse(file,headers={'Cache-Control':'no-store','Content-Type':mimetypes.guess_type(str(file))[0] or 'application/octet-stream'})


RIGHT_EDGES='''() => [...document.querySelectorAll('body *')].filter(e => {
 const cs=getComputedStyle(e); if(cs.display==='none'||cs.visibility==='hidden'||!e.getClientRects().length) return false;
 // Closed details descendants have layout boxes in Chrome, but do not paint.
 for(let p=e.parentElement;p;p=p.parentElement) if(p.tagName==='DETAILS'&&!p.open&&!p.querySelector('summary')?.contains(e)) return false;
 return true;
}).flatMap(e=>{const r=e.getBoundingClientRect();return r.width && (r.right>innerWidth+.5||r.left<-.5)?[`${e.tagName} ${e.dataset.testid||e.className} ${r.left.toFixed(1)}..${r.right.toFixed(1)}`]:[]}).slice(0,10)'''
ROWS='[data-testid^="holding-row-"], [data-testid^="active-swing-row-"], details[data-testid^="overnight-hold-"]'

def row_gate(page):
    expected=page.evaluate("async()=>Object.keys(await (await fetch('/api/positions')).json()).sort()")
    actual=page.locator(ROWS).evaluate_all("els=>els.map(e=>e.dataset.testid.replace(/^(holding-row-|active-swing-row-|overnight-hold-)/,'')).sort()")
    return actual==expected,actual,expected


def new_page(browser,server,fr,width,query='?intro=off',reduced=False,storage=None):
    server.show(fr)
    ctx=browser.new_context(viewport={'width':width,'height':844},timezone_id='America/New_York',reduced_motion='reduce' if reduced else 'no-preference')
    page=ctx.new_page();errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    if storage:page.add_init_script(storage)
    page.clock.set_fixed_time(fr['_fixed_now'] if fr else '2026-10-07T21:03:00-04:00')
    page.goto(URL+query,wait_until='networkidle')
    page.wait_for_selector('[data-testid="status-strip"]' if fr else '[data-testid="first-frame-skeleton"]')
    page.wait_for_timeout(100)
    return ctx,page,errors


# Verbatim legacy copy contracts retained from verify_overnight_holds.py (R6).
MOVE_TOGETHER = "IREN and HUT are both bitcoin miners and move together."
HOLD_THU = {
    "NVDA": "43 shares of NVDA bought at $228.87 at the close. No stop. Sells at the 9:30 AM open on Thu Oct 1.",
    "IREN": f"238 shares of IREN bought at $41.73 at the close. No stop. Sells at the 9:30 AM open on Thu Oct 1. {MOVE_TOGETHER}",
    "HUT": f"107 shares of HUT bought at $92.75 at the close. No stop. Sells at the 9:30 AM open on Thu Oct 1. {MOVE_TOGETHER}",
}
HOLD_WEEKEND_NVDA = "43 shares of NVDA bought at $228.87 at the close. No stop. Held over the weekend, sells at the 9:30 AM open on Mon Oct 5."
HOLD_HOLIDAY_NVDA = "43 shares of NVDA bought at $228.87 at the close. No stop. Held over the holiday, sells at the 9:30 AM open on Fri Nov 27."
TONIGHT = {
    "no_buy_closing": {s: "No buy tonight. You turned off tonight's buy." for s in ("NVDA", "IREN", "HUT")},
    "closing": {
        "NVDA": "Buy for 43 shares is waiting at Alpaca. It fills at the 4:00 PM close.",
        "IREN": "Buy for 238 shares is waiting at Alpaca. It fills at the 4:00 PM close.",
        "HUT": "Not bought yet. Too much of today's price data is missing. It keeps trying until 3:49:30 PM.",
    },
    "closing_late": {
        "NVDA": "Buy for 43 shares is waiting at Alpaca. It fills at the 4:00 PM close.",
        "IREN": "Buy for 238 shares is waiting at Alpaca. It fills at the 4:00 PM close.",
        "HUT": "No buy tonight. Too much of today's price data is missing.",
    },
}
TOO_LATE = "Too late to change tonight. It can only be changed until 3:49:30 PM."
ALREADY_STOPPED = "Tonight's buy was already stopped and cannot be restarted."
NOTHING_PLANNED = "No overnight buy is planned tonight."
REPLY_ON = "No overnight buy tonight. Holds already bought still sell at the next open."
REPLY_OFF = "The overnight buy is on for tonight."
NO_BUY_ON_LINE = "Tonight's buy is off. Holds already bought still sell at the next open."


def text(page,tid):return ' '.join(page.get_by_test_id(tid).inner_text().split())


def copy_checks(browser,server):
    # Existing helpers already use the later 3:59:30 market-buy timing. These explicit
    # deltas preserve that current truthful copy; see BUILD_REPORT's R6 conflict.
    current_hold=lambda s:s.replace(' at the close.', ' near the close.')
    cases=[('evening',HOLD_THU),('weekend',{'NVDA':HOLD_WEEKEND_NVDA})]
    hol=clean(old_frame('evening'))
    for h in hol['overnight']['holds']:h.update(sale_date='2026-11-27',nights='holiday')
    cases.append((hol,{'NVDA':HOLD_HOLIDAY_NVDA}))
    for name,wants in cases:
        fr=clean(old_frame(name)) if isinstance(name,str) else name
        ctx,p,_=new_page(browser,server,fr,390)
        try:
            for sym,want in wants.items():
                row=p.get_by_test_id('overnight-hold-'+sym);row.locator('summary').click()
                got=' '.join(row.get_by_test_id('overnight-hold-line').inner_text().split())
                check(got==current_hold(want),f'hold copy {sym} {fr["_fixed_now"]}')
        finally:ctx.close()
    for name in ['no_buy_closing','closing','closing_late']:
        fr=clean(old_frame(name));ctx,p,_=new_page(browser,server,fr,390)
        try:
            for sym,want in TONIGHT[name].items():
                if 'is waiting at Alpaca' in want:
                    want=want.replace('is waiting at Alpaca. It fills at the 4:00 PM close.','was sent near the close. Waiting for Alpaca.')
                want=want.replace('It keeps trying until 3:49:30 PM.','It keeps checking until 3:59:55 PM.')
                check(text(p,'overnight-tonight-'+sym)==f'{sym} overnight. {want}',f'tonight copy {name}/{sym}')
            if name!='closing':check(text(p,'overnight-no-buy-reason')==(ALREADY_STOPPED if name=='no_buy_closing' else TOO_LATE),f'no-buy reason {name}')
        finally:ctx.close()
    fr=clean(old_frame('day'))
    for r in fr['overnight']['rows']:r.update(state='SKIPPED',buy_date='2026-09-30',reason='NO_SIGNAL')
    ctx,p,_=new_page(browser,server,fr,390)
    try:check(text(p,'overnight-no-buy-reason')==NOTHING_PLANNED,'no buy planned reason verbatim')
    finally:ctx.close()


def matrix(browser,server,frames):
    SHOTS.mkdir(parents=True,exist_ok=True)
    for width in [1280,390,320]:
        for name,fr in frames.items():
            ctx,p,errors=new_page(browser,server,fr,width)
            try:
                label=f'{name}/{width}'
                check(not p.evaluate(RIGHT_EDGES),f'{label} every painted element within viewport {p.evaluate(RIGHT_EDGES)}')
                if fr is None:
                    body=p.inner_text('main')
                    check('Connecting to the robot…' in body and '$' not in body and 'Holding nothing' not in body,f'{label} first-frame skeleton has no invented numbers')
                    check(p.get_by_test_id('btn-flatten-all').count()==0,f'{label} no controls before first frame')
                else:
                    ok,actual,want=row_gate(p)
                    check(ok,f'{label} holdings {actual} equals /api/positions {want}')
                    count=1 if name in ['09-52','broker-mismatch'] else 0
                    pill=p.get_by_test_id('attention-pill')
                    check(pill.get_attribute('data-count')==str(count) and pill.inner_text()==('1 needs a look' if count else 'No alarms'),f'{label} alarm pill count and text')
                    check(p.get_by_test_id('btn-no-buy-tonight').count()==1,f'{label} exactly one Skip control')
                    closed=name in ['evening','sunday-open']
                    check(p.get_by_test_id('btn-flatten-all').is_disabled()==closed,f'{label} Close-all availability follows marketOpen')
                    check(p.get_by_test_id('playbook-panel').evaluate('e=>e.open')==(not closed),f'{label} initial playbook disclosure')
                    for pos in fr['all_positions']:
                        if pos.get('overnight'):
                            row=p.get_by_test_id('overnight-hold-'+pos['symbol'])
                            check(row.locator('button').count()==0,f'{label}/{pos["symbol"]} no overnight actions')
                            if closed:check('price at 9:30' in row.inner_text() and '$0' not in row.inner_text(),f'{label}/{pos["symbol"]} stale price hidden')
                    if name=='evening':
                        check('updated' in text(p,'freshness-pill') and 'stale' not in text(p,'freshness-pill'),f'{label} freshness uses receive time, not year-2000 server timestamp')
                        if width==390:check(p.evaluate('document.documentElement.scrollHeight')<=1300,f'{label} height {p.evaluate("document.documentElement.scrollHeight")} ≤ 1300')
                    if name=='09-52':check(text(p,'overnight-unsold-banner')=='IREN overnight did not sell at the 9:30 open. The robot keeps retrying. To sell it by hand use the Alpaca app.' and p.get_by_test_id('attention-list').locator('button').count()==0,f'{label} unsold copy and no action')
                    if name=='15-35':check(text(p,'lock-countdown').startswith('Overnight buy locks in 14 min'),f'{label} countdown from backend time')
                    if name=='sunday-open':check(bool(re.search(r'Market (?:is )?closed',text(p,'right-now-sentence'))),f'{label} Sunday OPEN is closed')
                    if name=='broker-mismatch':check('Broker and robot disagree' in p.inner_text('main') and 'Holding nothing' not in p.inner_text('main'),f'{label} broker mismatch never claims empty')
                check(not errors,f'{label} no page errors {errors}')
                if width in [1280,390]:p.screenshot(path=str(SHOTS/f'{name}-{width}.png'),full_page=True)
            finally:ctx.close()


def interaction_checks(browser,server,frames):
    ctx,p,_=new_page(browser,server,frames['15-35'],390)
    try:
        server.actions=[];server.posts=[]
        p.get_by_test_id('btn-flatten-all').click();p.wait_for_timeout(80)
        check(server.actions==[],'Close-all first tap sends nothing even while flat')
        p.get_by_test_id('btn-flatten-all').click();p.wait_for_timeout(100)
        check(server.actions==[{'action':'FLATTEN_ALL'}],'Close-all second tap sends unchanged payload while flat')
        p.get_by_test_id('btn-no-buy-tonight').click();p.wait_for_timeout(80)
        check(server.posts==[],'Skip first tap sends nothing')
        p.get_by_test_id('btn-no-buy-tonight').click();p.wait_for_timeout(150)
        check(server.posts==[{'on':True}] and text(p,'overnight-no-buy-reply')==REPLY_ON,'Skip POST and server reply preserved')
        check(text(p,'overnight-no-buy-on')==NO_BUY_ON_LINE,'skip on copy verbatim')
        p.get_by_test_id('btn-no-buy-tonight').click();p.wait_for_timeout(150)
        check(server.posts==[{'on':True},{'on':False}] and text(p,'overnight-no-buy-reply')==REPLY_OFF,'undo one tap and server reply preserved')
        server.reply_status=409;server.reply='Too late for tonight. The control can only be changed until 3:49:30 PM.'
        p.get_by_test_id('btn-no-buy-tonight').click();p.get_by_test_id('btn-no-buy-tonight').click();p.wait_for_timeout(150)
        check(text(p,'overnight-no-buy-reply')==server.reply,'409 plain reason remains visible')
        server.reply_status=200
        p.get_by_test_id('playbook-panel').locator('summary').first.click()
        server.show(frames['09-52']);p.wait_for_timeout(150)
        check(not p.get_by_test_id('playbook-panel').evaluate('e=>e.open'),'new real frame does not reset user playbook collapse')
        server.show(frames['evening']);p.wait_for_timeout(100)
        p.get_by_test_id('playbook-panel').locator('summary').first.click()
        server.show(frames['evening']);p.wait_for_timeout(100)
        check(p.get_by_test_id('playbook-panel').evaluate('e=>e.open'),'evening frame does not reset user playbook expansion')
        p.get_by_test_id('playbook-panel').locator('summary').first.click()
        bars=p.locator('[data-testid^="result-day-"]').evaluate_all("els=>els.reduce((n,e)=>n+Number(e.dataset.pnl),0)")
        p.get_by_test_id('open-full-history').click()
        check(p.get_by_test_id('history-explorer').is_visible() and p.get_by_test_id('status-strip').count()==0,'full history replaces Today')
        summary=p.get_by_test_id('history-explorer').inner_text()
        check(f'{abs(bars):,.2f}' in summary,'visible History Week finished total matches Results bars')
        check(not p.evaluate(RIGHT_EDGES),'History stays within 390px')
        p.get_by_test_id('back-to-today').click()
        check(p.get_by_test_id('status-strip').is_visible(),'Back returns to Today')
        check(not p.get_by_test_id('playbook-panel').evaluate('e=>e.open'),'History navigation preserves user disclosure choice')
    finally:server.reply_status=200;ctx.close()
    ctx,p,_=new_page(browser,server,frames['09-52'],390)
    try:
        server.actions=[]
        p.get_by_test_id('btn-sell-now-TSLA').click();p.wait_for_timeout(80)
        check(server.actions==[],'day close first tap sends nothing')
        p.get_by_test_id('btn-sell-now-TSLA').click();p.wait_for_timeout(100)
        check(server.actions==[{'action':'FLATTEN_POSITION','symbol':'TSLA'}],'day close handler preserved')
        # Strict mutation of the visible result gate, in addition to the source mutation below.
        p.get_by_test_id('overnight-hold-IREN').evaluate('e=>e.remove()')
        check(not row_gate(p)[0],'holdings gate catches a removed overnight row')
    finally:ctx.close()
    for key in ['evening','09-52']:
        ctx,p,_=new_page(browser,server,frames[key],390,query='')
        try:
            if key=='09-52':check(p.get_by_test_id('intro').count()==0 and p.evaluate("localStorage.getItem('cobaltIntroDate')")==None,'alarm frame skips intro entirely')
            else:
                check(p.evaluate("localStorage.getItem('cobaltIntroDate')")=='2026-10-07','intro stores ET date')
                p.wait_for_timeout(1300)
                check(p.get_by_test_id('intro').count()==0,'intro unmounts at 1.2 seconds')
                p.reload(wait_until='networkidle');check(p.get_by_test_id('intro').count()==0,'intro plays once per ET date')
        finally:ctx.close()
    ctx,p,_=new_page(browser,server,frames['evening'],390,query='',reduced=True)
    try:check(p.get_by_test_id('intro').count()==0,'reduced-motion skips intro')
    finally:ctx.close()


def supplemental_checks(browser,server,frames):
    mixed=copy.deepcopy(frames['evening'])
    day=position();day.update(market_price=day['entry_price'],unrealized_pnl=0,exit_due=None)
    slow=dict(symbol='MSFT',side='LONG',shares=5,entry_price=400,market_price=400,market_value=2000,
      unrealized_pnl=0,unrealized_pnl_pct=0,stop_loss=390,stop_loss_price=390,holding_days=2,
      max_holding_days=5,staged_exit_at_open=False,atr_14=5,atr_stop_distance=10,atr_stop_pct=2.5,
      holding_progress='2/5',sma_5=405,rsi_2=45,exit_triggers={})
    mixed['swing']['positions']=[slow]
    set_positions(mixed,mixed['all_positions']+[day,{**slow,'strategy_id':'swing_panic'}])
    mixed['all_positions'][0]['market_price']+=1
    for width in [390,320]:
        ctx,p,errors=new_page(browser,server,mixed,width)
        try:
            check(row_gate(p)[0],f'mixed/{width} overnight, day and swing positions counted once')
            check('last close price' in text(p,'holding-row-TSLA'),f'mixed/{width} day entry-equal price labelled')
            check('last close price' in text(p,'active-swing-row-MSFT'),f'mixed/{width} swing entry-equal price labelled')
            check('price at 9:30' in text(p,'overnight-hold-NVDA'),f'mixed/{width} unequal overnight price still stale')
            server.actions=[]
            p.get_by_test_id('btn-exit-open-MSFT').click();p.wait_for_timeout(80)
            p.get_by_test_id('btn-emergency-exit-MSFT').click();p.get_by_test_id('btn-emergency-exit-MSFT').click();p.wait_for_timeout(80)
            p.get_by_test_id('btn-tighten-stop-MSFT').click();p.get_by_test_id('input-raise-stop-MSFT').fill('395');p.get_by_test_id('btn-confirm-raise-MSFT').click();p.wait_for_timeout(80)
            check(server.actions==[{'action':'SWING_EXIT_NEXT_OPEN','symbol':'MSFT'},{'action':'SWING_EXIT_IMMEDIATE','symbol':'MSFT'},{'action':'SWING_TIGHTEN_STOP','symbol':'MSFT','new_stop':395}],f'mixed/{width} all three swing handlers preserved')
            p.get_by_test_id('pro-words-toggle').click()
            check('adaptive-v1.7.1' in text(p,'pro-details') and '483 rows' in text(p,'pro-details'),f'mixed/{width} ORB rules and research from live-shaped data')
            check(not p.evaluate(RIGHT_EDGES),f'mixed/{width} expanded pro details and swing fit {p.evaluate(RIGHT_EDGES)}')
            check(not errors,f'mixed/{width} no page errors')
        finally:ctx.close()
    ctx,p,_=new_page(browser,server,frames['evening'],390)
    try:
        p.clock.set_fixed_time('2026-10-07T21:05:00-04:00');p.wait_for_timeout(5200)
        check('stale 2 min' in text(p,'freshness-pill'),'lost freshness preserves last received numbers with amber pill')
        check(p.get_by_test_id('first-frame-skeleton').count()==0 and row_gate(p)[0],'stale socket retains data, no return to initial state')
        check(p.get_by_test_id('attention-pill').get_attribute('data-count')=='1','stale connection counts once, no extra freshness alarm')
    finally:ctx.close()
    server.ledger_error=True
    ctx,p,_=new_page(browser,server,frames['evening'],390)
    try:check(p.get_by_test_id('attention-pill').get_attribute('data-count')=='1' and 'results did not refresh' in p.inner_text('main').lower(),'7d fetch error reaches attention and Results')
    finally:ctx.close();server.ledger_error=False
    ctx,p,_=new_page(browser,server,frames['evening'],390,query='',storage="Storage.prototype.getItem=()=>{throw new Error('denied')};Storage.prototype.setItem=()=>{throw new Error('denied')}")
    try:
        p.wait_for_timeout(1300)
        check(p.get_by_test_id('intro').count()==0 and row_gate(p)[0],'storage denial does not block intro cleanup or dashboard')
    finally:ctx.close()


def source_mutation(browser,server,frames):
    """Actually rebuild with the old overnight exclusion; unchanged /api/positions must fail the row gate."""
    print('\n=== source mutation: re-add the old overnight exclusion',flush=True)
    with tempfile.TemporaryDirectory(prefix='gut-mutation-') as tmp:
        dest=Path(tmp)/'frontend'
        shutil.copytree(ROOT/'frontend',dest,ignore=shutil.ignore_patterns('node_modules','.next','out','*.tsbuildinfo'))
        os.symlink(ROOT/'frontend/node_modules',dest/'node_modules',target_is_directory=True)
        page=dest/'app/page.tsx';s=page.read_text()
        before='overnightPositions={state.all_positions.filter(isOvernightPosition)}'
        after='overnightPositions={state.all_positions.filter(p => !isOvernightPosition(p)).filter(isOvernightPosition)}'
        assert s.count(before)==1,'mutation target must be unique'
        page.write_text(s.replace(before,after))
        with (Path(tmp)/'build.log').open('w') as log:
            proc=subprocess.Popen(['npm','run','build'],cwd=dest,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            try:code=proc.wait(timeout=120)
            except BaseException:
                os.killpg(proc.pid,signal.SIGTERM)
                try:proc.wait(timeout=10)
                except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
                raise
        if code:raise RuntimeError('mutation build failed:\n'+(Path(tmp)/'build.log').read_text()[-4000:])
        original=server.out;server.out=dest/'out'
        try:
            ctx,p,_=new_page(browser,server,frames['evening'],390)
            try:
                ok,got,want=row_gate(p)
                check(not ok and len(got)==0 and len(want)==3,f'source mutation rejected: rendered {got}, API still {want}')
            finally:ctx.close()
        finally:server.out=original


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,default=ROOT/'frontend/out');parser.add_argument('--skip-mutation',action='store_true',help='development only; final gate runs mutation')
    args=parser.parse_args()
    if not (args.out/'index.html').exists():raise SystemExit('Build frontend/out first with npm run build')
    frames=fixtures()
    with FixtureServer(args.out) as server:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=CHROME if Path(CHROME).exists() else None,headless=True)
            try:
                matrix(browser,server,frames)
                copy_checks(browser,server)
                interaction_checks(browser,server,frames)
                supplemental_checks(browser,server,frames)
                if not args.skip_mutation:source_mutation(browser,server,frames)
            finally:browser.close()
    print(f'\n{PASSED} passed, {len(FAILURES)} failed',flush=True)
    for message in FAILURES:print('FAIL:',message)
    print('Fixture server stopped; browser closed; temporary mutation build removed.',flush=True)
    return 1 if FAILURES else 0

if __name__=='__main__':raise SystemExit(main())
