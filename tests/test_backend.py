"""All test data below is artificial. No provider records or fitted live model are shipped."""
import unittest,tempfile,json,math
from pathlib import Path
from datetime import datetime,timezone
import numpy as np
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.domain import Dataset,Source,NoteInput,NoteReview,timestamp
from backend.store import packed
from backend import analysis

BASE=timestamp('2026-11-10T01:00:00Z')
def iso(t):return datetime.fromtimestamp(t,timezone.utc).isoformat()
class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.now=BASE
        self.app=create_app(str(Path(self.tmp.name)/'test.sqlite'),'x'*40,lambda:self.now)
        self.s=self.app.state.store;self.client=TestClient(self.app);self.h={'Authorization':'Bearer '+'x'*40}
        self.s.register(Source(id='synthetic',name='Artificial test data',permission_reference='test-only generated fixture permission',permitted_uses=['display','analysis','training'],operator_attests_permission=True))
    def tearDown(self):self.client.close();self.tmp.cleanup()
    def ingest(self,kind,payload,rid='race1',effective=None):
        return self.s.ingest(Dataset(source_id='synthetic',records=[{'kind':kind,'race_id':rid,'effective_at':iso(self.now if effective is None else effective),'payload':payload}]))
    def fixture(self,n=7):
        self.start=BASE+3600
        self.ingest('race',{'venue':'人工テスト場','region':'中部','start':iso(self.start)})
        self.entries=[{'id':'p'+str(i),'no':i,'name':'Test '+str(i),'prefecture':'三重','race_points':80.+i} for i in range(1,n+1)]
        self.ingest('roster',{'entries':self.entries});self.ingest('lineup',{'lines':[[e['id'] for e in self.entries]]})
        for e in self.entries:self.ingest('stats',{'player_id':e['id'],'starts':20,'wins':4,'top3':10,'period_end':iso(BASE-86400)})
    def test_auth_permission_and_grants(self):
        self.assertEqual(self.client.get('/health').status_code,200)
        self.assertEqual(self.client.get('/v1/status').status_code,401)
        self.assertEqual(self.client.get('/v1/status',headers=self.h).status_code,200)
        no=create_app(str(Path(self.tmp.name)/'no.sqlite'),'short')
        with TestClient(no) as c:self.assertEqual(c.get('/v1/status').status_code,503)
        with self.assertRaises(ValueError):self.s.register(Source(id='bad',name='bad',permission_reference='not a license grant',permitted_uses=['display'],operator_attests_permission=False))
        d=Dataset(source_id='missing',records=[{'kind':'race','race_id':'r','effective_at':iso(self.now),'payload':{'venue':'x','region':'x','start':iso(self.now+10)}}])
        with self.assertRaises(ValueError):self.s.ingest(d)
        self.assertEqual(self.client.post('/v1/sources/synthetic/disable',headers=self.h).status_code,200)
        self.assertFalse(self.s.permitted('synthetic','training'))
    def test_capture_dedup_and_append_only(self):
        self.fixture();f,_=analysis.features(self.s,'race1',BASE+1);self.assertIsNotNone(f)
        self.assertEqual(self.ingest('lineup',{'lines':[[e['id'] for e in self.entries]]})['added'],0)
        with self.s.connect() as c:
            with self.assertRaises(Exception):c.execute('UPDATE events SET known_at=0')
        self.assertEqual(self.client.get('/v1/races?date=2026-11-10',headers=self.h).json()['total'],1)
    def test_late_information_not_backdated(self):
        self.fixture();before,_=analysis.features(self.s,'race1',BASE+100)
        self.now=self.start+500
        self.ingest('stats',{'player_id':'p1','starts':20,'wins':20,'top3':20,'period_end':iso(BASE-86400)},effective=BASE)
        after,_=analysis.features(self.s,'race1',BASE+100)
        self.assertEqual(before['x'],after['x'])
        # Importing an entire historical race today cannot establish a historical feature snapshot.
        self.ingest('race',{'venue':'past','region':'中部','start':iso(BASE-100)},rid='past',effective=BASE-1000)
        self.ingest('roster',{'entries':self.entries},rid='past',effective=BASE-1000)
        self.assertIsNone(self.s.snapshot('past',BASE-101))
    def test_result_before_start_and_atomic_rollback(self):
        self.fixture()
        with self.assertRaises(ValueError):self.ingest('result',{'order':['p1','p2','p3'],'payouts':[],'result_at':iso(BASE)})
        body={'source_id':'synthetic','records':[{'kind':'race','race_id':'new','effective_at':iso(BASE),'payload':{'venue':'new','region':'中部','start':iso(BASE+20)}},{'kind':'race','race_id':'future','effective_at':iso(BASE+100),'payload':{'venue':'new','region':'中部','start':iso(BASE+200)}}]}
        with self.assertRaises(ValueError):self.s.ingest(Dataset.model_validate(body))
        self.assertNotIn('new',self.s.race_ids())
    def test_unknown_follow_and_revision_reviews(self):
        self.fixture();a=analysis.race_analysis(self.s,'race1');self.assertIsNone(a['pairs'][0]['follow']['value'])
        note=self.s.add_note('note1',NoteInput(player_id='p1',content='test',evidence_reference='fixture source',evidence_excerpt='test excerpt'))
        review=NoteReview(revision=note['revision'],content_sha256=note['content_sha256'],reviewed_evidence_reference='fixture source',reviewer='test reviewer')
        self.s.review_note('note1',review);self.assertEqual(self.s.notes({'p1'},BASE)[0]['verification'],'operator_reviewed')
        self.s.add_note('note1',NoteInput(player_id='p1',content='edited',evidence_reference='fixture source',evidence_excerpt='test excerpt'))
        self.assertEqual(self.s.notes({'p1'},BASE)[0]['verification'],'unreviewed')
        with self.assertRaises(ValueError):self.s.review_note('note1',review)
    def test_model_normalization_calibration_and_insufficient_data(self):
        rng=np.random.default_rng(1);rows=[]
        for _ in range(80):
            x=rng.random((4,len(analysis.FEATURES)));rows.append({'x':x.tolist(),'target':[int(np.argmax(x[:,0])),1,2]})
        # Unique joint target, independent fixture labels.
        for r in rows:r['target']=[r['target'][0]]+[i for i in range(4) if i!=r['target'][0]][:2]
        w=analysis.fit(rows[:50]);t=analysis.calibrate(rows[50:65],w,3)
        p=analysis.joint(rows[-1]['x'],w,t,3);self.assertAlmostEqual(sum(p.values()),1);self.assertEqual(len(p),24)
        m=analysis.metrics(rows[65:],w,t,3);self.assertTrue(math.isfinite(m['brier_multiclass']))
        self.fixture();self.assertEqual(analysis.train(self.s)['status'],'insufficient_data')
    def approved_fixture_model(self):
        # Deliberately artificial model injected only into the isolated test database.
        body={'weights':[0.]*len(analysis.FEATURES),'temperatures':{'2':1.,'3':1.},'gates_passed':True}
        with self.s.connect() as c:c.execute('INSERT INTO models VALUES(?,?,?,?)',('testmodel',packed(body),BASE-1,BASE-1))
    def test_ken_odds_freshness_counts_and_paper_settlement(self):
        self.fixture();self.assertEqual(analysis.recommend(self.s,'race1')['decision'],'ケン');self.approved_fixture_model()
        self.assertEqual(analysis.recommend(self.s,'race1')['decision'],'ケン')
        from itertools import permutations
        for k,kind in [(2,'2車単'),(3,'3連単')]:
            for order in permutations(range(1,8),k):self.ingest('odds',{'type':kind,'selection':'-'.join(map(str,order)),'multiplier':1000.,'quoted_at':iso(BASE)})
        rec=analysis.recommend(self.s,'race1');self.assertEqual(rec['decision'],'買い候補');self.assertEqual([len(v) for v in rec['plans'].values()],[4,10,8])
        self.now=BASE+61;self.assertEqual(analysis.recommend(self.s,'race1')['decision'],'ケン')
        self.now=BASE+1;analysis.record_forecast(self.s,'race1',100)
        with self.assertRaises(ValueError):analysis.record_forecast(self.s,'race1',100)
        self.now=self.start+120
        self.ingest('result',{'order':[e['id'] for e in self.entries],'payouts':[{'type':'2車単','selection':'1-2','per100':100000},{'type':'3連単','selection':'1-2-3','per100':100000}],'result_at':iso(self.now)})
        bt=analysis.backtest(self.s);self.assertEqual(bt['cost'],2200);self.assertEqual(bt['paid'],200000);self.assertEqual(bt['status'],'settled')
    def test_full_chronological_training_pipeline_and_failed_approval(self):
        from backend.store import digest
        entries=[{'id':'p'+str(i),'no':i,'name':'Artificial '+str(i),'prefecture':'三重','race_points':80.+i} for i in range(1,8)]
        events=[]
        def add(kind,rid,payload,known):
            events.append(('synthetic',kind,rid,known,known,packed(payload),digest([kind,rid,payload,known])))
        for e in entries:add('stats','global',{'player_id':e['id'],'starts':20,'wins':4,'top3':10,'period_end':iso(BASE-86400)},BASE)
        for day in range(20):
            for j in range(50):
                rid='generated-'+str(day)+'-'+str(j);start=BASE+day*86400+3600+j*30;known=start-1800
                add('race',rid,{'venue':'人工場','region':'中部','start':iso(start)},known)
                add('roster',rid,{'entries':entries},known)
                add('lineup',rid,{'lines':[[e['id'] for e in entries]]},known)
                add('result',rid,{'order':[e['id'] for e in reversed(entries)],'payouts':[],'cancelled':False,'result_at':iso(start+5)},start+5)
        with self.s.connect() as c:c.executemany('INSERT INTO events(source_id,kind,race_id,effective_at,known_at,body,hash) VALUES(?,?,?,?,?,?,?)',events)
        self.now=BASE+21*86400
        r=analysis.train(self.s)
        self.assertEqual(r['status'],'evaluated');self.assertEqual(r['split_counts'],[600,200,200]);self.assertFalse(r['notes_in_probability'])
        self.assertEqual(r['source_ids'],['synthetic']);self.assertFalse(r['gates_passed'])
        self.assertEqual(self.client.post('/v1/models/'+r['id']+'/approve',headers=self.h).status_code,409)
        self.assertIsNone(analysis.model(self.s,True))

    def test_simulation_unavailable_and_body_validation(self):
        self.fixture();self.assertEqual(analysis.simulate(self.s,'race1')['status'],'unavailable')
        self.assertEqual(self.client.post('/v1/import',headers=self.h,json={'source_id':'synthetic','records':[]}).status_code,422)
        self.assertEqual(self.client.post('/v1/import',headers=self.h,content=b'x'*(5*1024*1024+1)).status_code,413)
        self.assertEqual(self.client.get('/health').headers['X-Content-Type-Options'],'nosniff')

if __name__=='__main__':unittest.main()
