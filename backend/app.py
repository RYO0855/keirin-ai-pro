"""Deploy separately from GitHub Pages. API keys never belong in the repository."""
import os,time,hmac,json,uuid,threading,sqlite3
from collections import defaultdict,deque
from pathlib import Path
from fastapi import FastAPI,Depends,HTTPException,Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from .domain import Source,Dataset,NoteInput,NoteReview,ForecastRequest,timestamp
from .store import Store
from . import analysis

class BoundedBody:
    def __init__(self,app,limit=5*1024*1024):self.app=app;self.limit=limit
    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or scope['method'] not in ['POST','PUT','PATCH']:
            return await self.app(scope,receive,send)
        data=bytearray()
        while True:
            m=await receive()
            if m['type']=='http.disconnect':return
            data.extend(m.get('body',b''))
            if len(data)>self.limit:
                await JSONResponse({'detail':'Request exceeds 5MB'},status_code=413)(scope,receive,send);return
            if not m.get('more_body',False):break
        sent=False
        async def replay():
            nonlocal sent
            if not sent:sent=True;return {'type':'http.request','body':bytes(data),'more_body':False}
            return await receive()
        await self.app(scope,replay,send)

def create_app(db_path=None,token=None,clock=time.time):
    app=FastAPI(title='KEIRIN AI PRO licensed-data API',version='2.0',docs_url=None,redoc_url=None,openapi_url=None)
    app.state.store=Store(db_path or os.getenv('KEIRIN_DB','data/keirin.sqlite'),clock)
    key=token if token is not None else os.getenv('KEIRIN_API_KEY','')
    limits=defaultdict(deque);rate_lock=threading.Lock();train_lock=threading.Lock()
    origins=[o.strip() for o in os.getenv('KEIRIN_ALLOWED_ORIGINS','').split(',') if o.strip()]
    if '*' in origins:raise ValueError('Explicit CORS origins required')
    app.add_middleware(CORSMiddleware,allow_origins=origins,allow_credentials=False,allow_methods=['GET','POST','PUT'],allow_headers=['Authorization','Content-Type'])
    app.add_middleware(BoundedBody)
    @app.middleware('http')
    async def security(request,call_next):
        response=await call_next(request)
        response.headers.update({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','Content-Security-Policy':"default-src 'none'; frame-ancestors 'none'"})
        return response
    def authorised(request:Request):
        if len(key)<32:raise HTTPException(503,'API key is not configured (minimum 32 characters)')
        supplied=request.headers.get('Authorization','')
        if not hmac.compare_digest(supplied.encode(),('Bearer '+key).encode()):raise HTTPException(401,'Authentication required')
        bucket='train' if request.url.path=='/v1/models/train' else ('write' if request.method!='GET' else 'read')
        interval,maximum=(3600,2) if bucket=='train' else (60,30 if bucket=='write' else 120)
        ident=(request.client.host if request.client else 'unknown',bucket);now=time.monotonic()
        with rate_lock:
            q=limits[ident]
            while q and q[0]<now-interval:q.popleft()
            if len(q)>=maximum:raise HTTPException(429,'Rate limit exceeded',headers={'Retry-After':str(interval)})
            q.append(now)
    @app.exception_handler(sqlite3.IntegrityError)
    async def conflict(request,exc):return JSONResponse({'detail':'Record already exists or conflicts with an immutable record'},status_code=409)
    @app.exception_handler(ValueError)
    async def invalid(request,exc):return JSONResponse({'detail':str(exc)[:500]},status_code=422)
    @app.get('/health')
    def health():
        return {'status':'ready' if len(key)>=32 else 'configuration_required','api_version':'2.0','licensed_sources':len(app.state.store.sources()),'model':('approved' if analysis.model(app.state.store,True) else 'unavailable'),'official_api':'利用許諾付きの一般公開API未確認','odds_feed':'未接続（許可済みデータの取り込みに対応）','sample_data_in_backend':False}
    def store():return app.state.store
    auth=[Depends(authorised)]
    @app.get('/v1/status',dependencies=auth)
    def status():
        s=store();m=analysis.model(s)
        return {'sources':s.sources(),'races':len(s.race_ids()),'model':m,'notes_policy':'operator-reviewed evidence, excluded from probability features','knowledge_time':'server ingestion time, append-only, not backdated','source_audit':[{'name':'KEIRIN.JP','url':'https://keirin.jp/pc/dfw/portal/guest/policy/index.html','status':'複製・転用制限。許諾・API契約なし。自動収集未実装'},{'name':'yomisuji','url':'https://keirin.yomisuji.jp/about/','status':'二次利用ライセンス未確認。既存ブラウザ参照のみ。学習・再配信に使用しない'}]}
    @app.post('/v1/sources',dependencies=auth)
    def register_source(data:Source):return store().register(data)
    @app.post('/v1/sources/{source_id}/disable',dependencies=auth)
    def disable_source(source_id:str):
        with store().connect() as c:c.execute('INSERT OR IGNORE INTO source_disabled VALUES(?,?)',(source_id,store().clock()))
        return {'id':source_id,'status':'disabled','historical_events_retained':True}
    @app.post('/v1/import',dependencies=auth)
    def ingest(data:Dataset):return store().ingest(data)
    @app.get('/v1/races',dependencies=auth)
    def races(date:str|None=None,offset:int=0,limit:int=500):
        if offset<0 or not 1<=limit<=1000:raise HTTPException(422,'Invalid pagination')
        if date:
            from datetime import datetime
            try:datetime.strptime(date,'%Y-%m-%d')
            except ValueError:raise HTTPException(422,'YYYY-MM-DD required')
        rows=[]
        from datetime import datetime,timezone,timedelta
        for rid in store().race_ids():
            s=store().snapshot(rid)
            if not s:continue
            day=datetime.fromtimestamp(timestamp(s['start']),timezone(timedelta(hours=9))).date().isoformat()
            if date and day!=date:continue
            rows.append(s|{'date':day})
        rows.sort(key=lambda r:(timestamp(r['start']),r['id']))
        return {'total':len(rows),'offset':offset,'races':rows[offset:offset+limit],'complete_nationwide_feed':False}
    @app.get('/v1/races/{race_id}/analysis',dependencies=auth)
    def race_analysis(race_id:str):
        out=analysis.race_analysis(store(),race_id)
        if out is None:raise HTTPException(404,'Race not found')
        return out
    @app.post('/v1/races/{race_id}/simulate',dependencies=auth)
    def simulation(race_id:str):return analysis.simulate(store(),race_id)
    @app.post('/v1/models/train',dependencies=auth)
    def train_model():
        if not train_lock.acquire(blocking=False):raise HTTPException(409,'Training already running')
        try:return analysis.train(store())
        finally:train_lock.release()
    @app.post('/v1/models/{model_id}/approve',dependencies=auth)
    def approve(model_id:str):
        with store().connect() as c:
            row=c.execute('SELECT * FROM models WHERE id=?',(model_id,)).fetchone()
            if not row:raise HTTPException(404,'Model not found')
            a=json.loads(row['body'])
            if not a['gates_passed'] or any(not store().permitted(sid,'training') for sid in a.get('source_ids',[])):raise HTTPException(409,'Holdout/calibration or source permission gates failed')
            c.execute('UPDATE models SET approved_at=COALESCE(approved_at,?) WHERE id=?',(store().clock(),model_id))
        return {'id':model_id,'status':'operator_approved','profits_guaranteed':False}
    @app.post('/v1/forecasts',dependencies=auth)
    def forecast(data:ForecastRequest):return analysis.record_forecast(store(),data.race_id,data.stake_per_ticket)
    @app.get('/v1/backtest',dependencies=auth)
    def backtest():return analysis.backtest(store())
    @app.post('/v1/notes',dependencies=auth)
    def create_note(data:NoteInput):return store().add_note(uuid.uuid4().hex,data)
    @app.put('/v1/notes/{note_id}',dependencies=auth)
    def edit_note(note_id:str,data:NoteInput):return store().add_note(note_id,data)
    @app.post('/v1/notes/{note_id}/review',dependencies=auth)
    def review(note_id:str,data:NoteReview):return store().review_note(note_id,data)
    return app
