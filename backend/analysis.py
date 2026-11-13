"""Point-in-time features and experimental Plackett–Luce ranking model."""
from itertools import permutations
from datetime import datetime, timezone, timedelta
import json, math, numpy as np
from .domain import timestamp
from .store import packed,digest

FEATURES=['race_points_scaled','win_rate','top3_rate','line_size','line_position','follow_rate_masked','follow_known','drop_rate_masked','drop_known','same_prefecture_masked','prefecture_known']
PREF_REGIONS={}
for region,prefs in {'北日本':'北海道 青森 岩手 宮城 秋田 山形 福島','関東':'茨城 栃木 群馬 埼玉 東京 新潟 長野 山梨','南関東':'千葉 神奈川 静岡','中部':'愛知 岐阜 三重 富山 石川','近畿':'福井 滋賀 京都 大阪 兵庫 奈良 和歌山','中国':'鳥取 島根 岡山 広島 山口','四国':'徳島 香川 愛媛 高知','九州':'福岡 佐賀 長崎 熊本 大分 宮崎 鹿児島 沖縄'}.items():
    for p in prefs.split():PREF_REGIONS[p]=region

def ratio(rows,key):
    a=[r['payload'][key] for r in rows if isinstance(r['payload'][key],bool)]
    return {'value':sum(a)/len(a) if a else None,'n':len(a)}

def history(store,leader,follower,cutoff,training=False):
    rows=store.global_events('follow',cutoff,training);out=[];seen=set()
    for row in rows:
        p=row['payload'];key=(row['race_id'],p['leader'],p['follower'])
        if key in seen:continue
        seen.add(key)
        if p['leader']==leader and p['follower']==follower:out.append(row)
    return out

def features(store,race_id,cutoff,training=False):
    s=store.snapshot(race_id,cutoff,training)
    if not s or len(s['entries'])<3 or not s['lines']:return None,'出走表・ライン不足'
    if timestamp(s['start'])<=cutoff:cutoff=timestamp(s['start'])-0.001
    # Rebuild after lowering cutoff: never use a roster obtained after start.
    s=store.snapshot(race_id,cutoff,training)
    if not s or len(s['entries'])<3 or not s['lines']:return None,'発走前に保存された出走表・ラインなし'
    ids={e['id'] for e in s['entries']}
    if {x for g in s['lines'] for x in g}!=ids:return None,'全選手のライン所属が必要'
    stats=store.global_events('stats',cutoff,training);latest={}
    for x in stats:
        p=x['payload']
        if p['player_id'] not in latest and timestamp(p['period_end'])<timestamp(s['start']):latest[p['player_id']]=x
    vectors=[];lineage=[];source_ids=set(s['source_ids'])
    for e in s['entries']:
        stat=latest.get(e['id'])
        if e['race_points'] is None or not stat:return None,'選手成績不足'
        source_ids.add(stat['source_id'])
        p=stat['payload'];g=next(g for g in s['lines'] if e['id'] in g);pos=g.index(e['id'])
        leader=g[pos-1] if pos else None
        rows=history(store,leader,e['id'],cutoff,training) if leader else []
        source_ids.update(x['source_id'] for x in rows)
        f,d=ratio(rows,'followed'),ratio(rows,'dropped')
        a=next((x['prefecture'] for x in s['entries'] if x['id']==leader),None);b=e['prefecture'];known=bool(a and b)
        vectors.append([e['race_points']/100,p['wins']/p['starts'],p['top3']/p['starts'],len(g)/len(ids),1/(pos+1),f['value'] if f['n'] else 0,float(bool(f['n'])),d['value'] if d['n'] else 0,float(bool(d['n'])),float(a==b) if known else 0,float(known)])
        lineage.append({'stats_event':stat['id'],'follow_events':[x['id'] for x in rows]})
    return {'snapshot':s,'x':vectors,'lineage':lineage,'feature_cutoff':cutoff,'source_ids':sorted(source_ids)},None

def softmax(v):
    a=np.asarray(v,dtype=float);a=a-np.max(a);p=np.exp(a);return p/p.sum()

def joint(x,weights,temperature,k):
    strengths=softmax(np.asarray(x)@np.asarray(weights)/temperature)
    out={}
    for order in permutations(range(len(x)),k):
        remaining=1.;p=1.
        for i in order:
            p*=float(strengths[i])/max(remaining,1e-15);remaining-=float(strengths[i])
        out[order]=p
    total=sum(out.values())
    return {k:v/total for k,v in out.items()}

def fit(rows):
    w=np.zeros(len(FEATURES))
    for _ in range(350):
        g=.01*w
        for r in rows:
            x=np.asarray(r['x']);p=softmax(x@w);g+=(p@x-x[r['target'][0]])/len(rows)
        w-=.3*g
    return w.tolist()

def calibrate(rows,w,k):
    candidates=np.geomspace(.4,4,40)
    return float(min(candidates,key=lambda t:sum(-math.log(max(joint(r['x'],w,float(t),k)[tuple(r['target'][:k])],1e-15)) for r in rows)))

def metrics(rows,w,temp,k):
    losses=[];briers=[];uniform=[];conf=[];hits=[]
    for r in rows:
        ps=joint(r['x'],w,temp,k);target=tuple(r['target'][:k]);p=ps[target]
        losses.append(-math.log(max(p,1e-15)));briers.append(sum(v*v for v in ps.values())-2*p+1);uniform.append(math.log(len(ps)))
        choice=max(ps,key=ps.get);conf.append(ps[choice]);hits.append(int(choice==target))
    ece=0.
    for low in np.linspace(0,.9,10):
        idx=[i for i,c in enumerate(conf) if low<=c<(low+.1 if low<.9 else 1.000001)]
        if idx:ece+=len(idx)/len(rows)*abs(np.mean([conf[i] for i in idx])-np.mean([hits[i] for i in idx]))
    return {'races':len(rows),'log_loss':float(np.mean(losses)),'uniform_log_loss':float(np.mean(uniform)),'brier_multiclass':float(np.mean(briers)),'top_choice_ece_10bins':float(ece),'top_choice_accuracy':float(np.mean(hits))}

def train(store):
    now=store.clock();rows=[];excluded={}
    if len(store.race_ids())>5000:return {'status':'offline_job_required','reason':'5000レース超はサーバー負荷を避けて専用バッチで処理してください'}
    for rid in store.race_ids():
        result=store.latest(rid,'result',now,True);s=store.snapshot(rid,now,True)
        if not result or not s or result['payload']['cancelled']:continue
        f,reason=features(store,rid,timestamp(s['start'])-.001,True)
        if not f:excluded[reason]=excluded.get(reason,0)+1;continue
        ids=[e['id'] for e in f['snapshot']['entries']];order=result['payload']['order']
        if len(order)<3 or any(p not in ids for p in order[:3]):continue
        rows.append({'race_id':rid,'start':timestamp(s['start']),'day':datetime.fromtimestamp(timestamp(s['start']),timezone(timedelta(hours=9))).date().isoformat(),'x':f['x'],'target':[ids.index(x) for x in order[:3]],'result_known_at':result['known_at'],'lineage':f['lineage'],'snapshot_provenance':f['snapshot']['provenance'],'source_ids':sorted(set(f['source_ids']+[result['source_id']]))})
    rows.sort(key=lambda x:(x['start'],x['race_id']));days=sorted({r['day'] for r in rows})
    if len(days)<5 or len(rows)<700:return {'status':'insufficient_data','eligible_races':len(rows),'excluded':excluded,'required':'train>=500 / calibration>=100 / test>=100, grouped by JST day; immutable pre-start snapshots required'}
    cday=days[int(len(days)*.6)];tday=days[int(len(days)*.8)];cal_start=min(r['start'] for r in rows if r['day']>=cday);test_start=min(r['start'] for r in rows if r['day']>=tday)
    tr=[r for r in rows if r['day']<cday and r['result_known_at']<cal_start]
    ca=[r for r in rows if cday<=r['day']<tday and r['result_known_at']<test_start]
    te=[r for r in rows if r['day']>=tday]
    if min(len(tr)/500,len(ca)/100,len(te)/100)<1:return {'status':'insufficient_data','split_counts':[len(tr),len(ca),len(te)],'excluded':excluded}
    weights=fit(tr);temperatures={str(k):calibrate(ca,weights,k) for k in [2,3]}
    reports={str(k):{'uncalibrated_test':metrics(te,weights,1.,k),'calibrated_test':metrics(te,weights,temperatures[str(k)],k)} for k in [2,3]}
    passed=all(r['calibrated_test']['log_loss']<r['calibrated_test']['uniform_log_loss'] and r['calibrated_test']['top_choice_ece_10bins']<=.1 for r in reports.values())
    artifact={'algorithm':'pl-winner-linear-v1','features':FEATURES,'weights':weights,'temperatures':temperatures,'reports':reports,'split_counts':[len(tr),len(ca),len(te)],'calibration_start':cal_start,'test_start':test_start,'source_ids':sorted({sid for r in rows for sid in r['source_ids']}),'training_data_hash':digest(tr+ca+te),'gates_passed':passed,'status':'evaluated','notes_in_probability':False,'evaluation_type':'retrospective_point_in_time_reconstruction','excluded':excluded}
    model_id=digest(artifact)[:24]
    with store.connect() as c:c.execute('INSERT OR IGNORE INTO models VALUES(?,?,?,NULL)',(model_id,packed(artifact),now))
    return {'id':model_id,**artifact,'created_at':now,'approved_at':None}

def model(store,approved=False):
    with store.connect() as c:
        r=c.execute('SELECT * FROM models '+('WHERE approved_at IS NOT NULL AND approved_at<=? AND created_at<=? ORDER BY approved_at DESC' if approved else 'ORDER BY created_at DESC')+' LIMIT 1', (store.clock(),store.clock()) if approved else ()).fetchone()
    if not r:return None
    body=json.loads(r['body'])
    if approved and any(not store.permitted(sid,'training') for sid in body.get('source_ids',[])):return None
    return {'id':r['id'],**body,'created_at':r['created_at'],'approved_at':r['approved_at']}

def recommend(store,rid,as_of=None):
    cut=store.clock() if as_of is None else min(as_of,store.clock());s=store.snapshot(rid,cut)
    out={'decision':'ケン','reasons':[],'plans':{'exacta':[],'trifecta':[],'holes':[]},'required_counts':{'exacta':4,'trifecta':10,'holes':8},'probabilities':None,'odds_status':'未接続または有効な取得データなし'}
    if not s:return out|{'reasons':['出走表未取得']}
    if timestamp(s['start'])<=cut:return out|{'reasons':['発走済みまたは締切後']}
    m=model(store,True)
    if not m:return out|{'reasons':['校正・評価・運用承認済みモデルなし']}
    f,reason=features(store,rid,cut)
    if not f:return out|{'reasons':[reason]}
    odds={}
    for row in store.events(rid,'odds',cut):
        p=row['payload'];key=(p['type'],p['selection'])
        if key not in odds:odds[key]=row
    valid={k:r for k,r in odds.items() if 0<=cut-timestamp(r['payload']['quoted_at'])<=60}
    if not valid:return out|{'reasons':['60秒以内の実オッズなし']}
    out['odds_status']='取り込み済み実オッズ（最大60秒前）';out['model_id']=m['id'];out['snapshot_provenance']=s['provenance']
    all_candidates={}
    for k,kind in [(2,'2車単'),(3,'3連単')]:
        candidates=[];distribution=joint(f['x'],m['weights'],m['temperatures'][str(k)],k)
        for order,p in distribution.items():
            selection='-'.join(str(s['entries'][i]['no']) for i in order);row=valid.get((kind,selection))
            if not row:continue
            o=row['payload'];ev=p*o['multiplier']-1
            if ev>=.05:candidates.append({'type':kind,'selection':selection,'probability':p,'odds':o['multiplier'],'odds_event_id':row['id'],'quoted_at':o['quoted_at'],'expected_return':ev})
        all_candidates[k]=sorted(candidates,key=lambda x:(-x['expected_return'],x['selection']))
    exacta=all_candidates[2][:4];trifecta=all_candidates[3][:10];used={x['selection'] for x in trifecta}
    # Hole tickets: lower-probability half of the available positive-EV triple pool.
    remaining=[x for x in all_candidates[3] if x['selection'] not in used]
    median=float(np.median([x['probability'] for x in all_candidates[3]])) if all_candidates[3] else 0
    holes=[x for x in remaining if x['probability']<=median][:8]
    out['plans']={'exacta':exacta,'trifecta':trifecta,'holes':holes};out['probabilities']='校正済みモデル推定（利益保証なし）'
    if [len(exacta),len(trifecta),len(holes)]==[4,10,8]:out['decision']='買い候補'
    else:out['reasons'].append('期待収益率5%以上・重複なしで必要点数に未達')
    return out

def race_analysis(store,rid):
    s=store.snapshot(rid)
    if not s:return None
    cut=min(store.clock(),timestamp(s['start'])-.001);pair_rows=[]
    for g in s['lines']:
        for i in range(1,len(g)):
            a,b=g[i-1],g[i];rows=history(store,a,b,cut);pa=next(e['prefecture'] for e in s['entries'] if e['id']==a);pb=next(e['prefecture'] for e in s['entries'] if e['id']==b)
            pair_rows.append({'leader':a,'follower':b,'count':len(rows),'follow':ratio(rows,'followed'),'bante_follow':ratio([x for x in rows if x['payload']['position']==2],'followed'),'drop':ratio(rows,'dropped'),'same_prefecture':pa==pb if pa and pb else None,'same_region':PREF_REGIONS[pa]==PREF_REGIONS[pb] if pa in PREF_REGIONS and pb in PREF_REGIONS else None})
    stats=[]
    for e in s['entries']:
        rows=[r for r in store.global_events('stats',cut) if r['payload']['player_id']==e['id']]
        stats.append({'player_id':e['id'],'statistics':rows[0]['payload'] if rows else None})
    result=store.latest(rid,'result')
    return {'race':s,'pairs':pair_rows,'statistics':stats,'result':result['payload'] if result else None,'recommendation':recommend(store,rid),'scenarios':[{'leader':g[0],'line':g,'probability':None,'status':'構成に基づく仮説・実展開は未検証'} for g in s['lines']],'reviewed_notes':[n for n in store.notes({e['id'] for e in s['entries']},cut) if n['verification']=='operator_reviewed'],'simulation_status':'モデル承認後のみ順位シミュレーション可。風・先行争いを含む走行力学モデルは未実装'}

def simulate(store,rid,runs=5000):
    s=store.snapshot(rid);m=model(store,True)
    if not s or timestamp(s['start'])<=store.clock() or not m:return {'status':'unavailable','reason':'発走前データ・承認済みモデルが必要'}
    f,reason=features(store,rid,store.clock())
    if not f:return {'status':'unavailable','reason':reason}
    strengths=softmax(np.asarray(f['x'])@np.asarray(m['weights'])/m['temperatures']['3']);rng=np.random.default_rng(20261113);counts={e['id']:0 for e in s['entries']}
    for _ in range(runs):
        order=np.argsort(-np.log(np.maximum(rng.random(len(strengths)),1e-15))/strengths)
        for i in order[:3]:counts[s['entries'][int(i)]['id']]+=1
    return {'status':'model_simulation','model_id':m['id'],'runs':runs,'seed':20261113,'type':'Plackett-Luce順位のみ・実走行再現ではない','top3_rates':{k:v/runs for k,v in counts.items()},'notes_in_probability':False}

def record_forecast(store,rid,stake):
    plan=recommend(store,rid)
    if plan['decision']!='買い候補':raise ValueError('ケン判定のため事前検討記録を生成しません')
    now=store.clock();snapshot=store.snapshot(rid,now)
    if not snapshot or now>=timestamp(snapshot['start']) or any(now-timestamp(t['quoted_at'])>60 for group in plan['plans'].values() for t in group):raise ValueError('発走時刻またはオッズ有効期限を超過しました')
    body={'plan':plan,'stake':stake,'predicted_at':now}
    with store.connect() as c:
        if c.execute('SELECT 1 FROM forecasts WHERE race_id=?',(rid,)).fetchone():raise ValueError('A forecast for this race already exists; duplicates are prohibited')
        cur=c.execute('INSERT INTO forecasts(race_id,model_id,body,known_at) VALUES(?,?,?,?)',(rid,plan['model_id'],packed(body),now))
        return {'id':cur.lastrowid,**body,'real_purchase':False}

def backtest(store):
    with store.connect() as c:rows=c.execute('SELECT * FROM forecasts ORDER BY known_at,id').fetchall()
    cost=paid=hits=0;peak=dd=0;details=[];excluded=[]
    for row in rows:
        f=json.loads(row['body']);rid=row['race_id'];s=store.snapshot(rid,row['known_at']);result=store.latest(rid,'result');m=None
        with store.connect() as c:m=c.execute('SELECT * FROM models WHERE id=?',(row['model_id'],)).fetchone()
        if not s or not result or not m or m['approved_at'] is None or m['approved_at']>row['known_at'] or m['created_at']>row['known_at'] or not row['known_at']<timestamp(s['start']):excluded.append(rid);continue
        if result['known_at']<=row['known_at'] or timestamp(result['payload']['result_at'])<timestamp(s['start']):excluded.append(rid);continue
        if result['payload']['cancelled']:excluded.append(rid);continue
        payouts={(p['type'],p['selection']):p['per100'] for p in result['payload']['payouts']};entries={e['id']:e['no'] for e in s['entries']};order=result['payload']['order']
        if any(x not in entries for x in order):excluded.append(rid);continue
        tickets=[x for group in f['plan']['plans'].values() for x in group];settlements=[]
        for t in tickets:
            k=2 if t['type']=='2車単' else 3
            actual='-'.join(str(entries[x]) for x in order[:k]);winning=(t['type'],actual)
            if len(order)<k or winning not in payouts:break
            # Stored quote was known before forecast and no more than 60 seconds old.
            quote=next((e for e in store.events(rid,'odds',row['known_at']) if e['id']==t['odds_event_id']),None)
            if not quote or row['known_at']-timestamp(quote['payload']['quoted_at'])>60:break
            hit=t['selection']==actual;settlements.append((hit,payouts[winning]*f['stake']/100 if hit else 0))
        if len(settlements)!=len(tickets):excluded.append(rid);continue
        race_cost=len(tickets)*f['stake'];race_paid=sum(p for _,p in settlements);cost+=race_cost;paid+=race_paid;hits+=sum(h for h,_ in settlements);peak=max(peak,paid-cost);dd=max(dd,peak-(paid-cost));details.append({'race_id':rid,'cost':race_cost,'paid':race_paid,'tickets':len(tickets)})
    return {'status':'settled' if details else 'insufficient_data','cost':cost,'paid':paid,'net':paid-cost,'return_rate':paid/cost if cost else None,'max_drawdown':dd,'hits':hits,'races':details,'excluded':excluded,'refunds_ties_and_partial_cancellations':'未対応・対象外。確定払戻不足は除外','paper_trading':True}
