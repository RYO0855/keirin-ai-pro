"""Append-only knowledge log. known_at is set by the server, never by an import."""
import sqlite3,json,hashlib,time
from pathlib import Path
from .domain import timestamp

def packed(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def digest(v):return hashlib.sha256(packed(v).encode()).hexdigest()

class Store:
    def __init__(self,path,clock=time.time):
        self.path=path;self.clock=clock
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as c:
            c.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY,body TEXT NOT NULL,registered_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS source_disabled(id TEXT PRIMARY KEY,disabled_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,source_id TEXT NOT NULL,kind TEXT NOT NULL,race_id TEXT NOT NULL,effective_at REAL NOT NULL,known_at REAL NOT NULL,body TEXT NOT NULL,hash TEXT NOT NULL UNIQUE);
            CREATE INDEX IF NOT EXISTS events_lookup ON events(race_id,kind,known_at,effective_at);
            CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'append-only');END;
            CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'append-only');END;
            CREATE TABLE IF NOT EXISTS note_revisions(id INTEGER PRIMARY KEY,note_id TEXT NOT NULL,revision INTEGER NOT NULL,body TEXT NOT NULL,hash TEXT NOT NULL,known_at REAL NOT NULL,UNIQUE(note_id,revision));
            CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY,note_id TEXT NOT NULL,revision INTEGER NOT NULL,hash TEXT NOT NULL,body TEXT NOT NULL,known_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS models(id TEXT PRIMARY KEY,body TEXT NOT NULL,created_at REAL NOT NULL,approved_at REAL);
            CREATE TABLE IF NOT EXISTS forecasts(id INTEGER PRIMARY KEY,race_id TEXT NOT NULL,model_id TEXT NOT NULL,body TEXT NOT NULL,known_at REAL NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS forecasts_race_unique ON forecasts(race_id);
            CREATE TRIGGER IF NOT EXISTS forecasts_no_update BEFORE UPDATE ON forecasts BEGIN SELECT RAISE(ABORT,'append-only');END;
            CREATE TRIGGER IF NOT EXISTS forecasts_no_delete BEFORE DELETE ON forecasts BEGIN SELECT RAISE(ABORT,'append-only');END;
            ''')
    def connect(self):
        c=sqlite3.connect(self.path,timeout=10);c.row_factory=sqlite3.Row
        return c
    def sources(self):
        with self.connect() as c:return [json.loads(r['body']) for r in c.execute('SELECT body FROM sources ORDER BY id')]
    def register(self,source):
        body=source.model_dump()
        with self.connect() as c:
            old=c.execute('SELECT body FROM sources WHERE id=?',(source.id,)).fetchone()
            if old and old['body']!=packed(body):raise ValueError('Source grants are immutable; use a new source ID for a revised grant')
            c.execute('INSERT OR IGNORE INTO sources VALUES(?,?,?)',(source.id,packed(body),self.clock()))
        return body
    def permitted(self,source_id,use):
        with self.connect() as c:
            r=c.execute('SELECT body FROM sources WHERE id=?',(source_id,)).fetchone()
            disabled=c.execute('SELECT 1 FROM source_disabled WHERE id=?',(source_id,)).fetchone()
        return bool(not disabled and r and use in json.loads(r['body'])['permitted_uses'])
    def ingest(self,data):
        if not all(self.permitted(data.source_id,u) for u in ['display','analysis']):
            raise ValueError('Registered display/analysis permission is required')
        now=self.clock();added=0
        with self.connect() as c:
            for r in data.records:
                effective=timestamp(r.effective_at)
                if effective>now:raise ValueError('effective_at cannot be in the future; a future race start belongs in the race payload')
                p=r.payload
                if r.kind=='odds':
                    if timestamp(p['quoted_at'])>now or abs(effective-timestamp(p['quoted_at']))>1:raise ValueError('Odds time mismatch')
                if r.kind in ['follow','result']:
                    if timestamp(p['result_at'])>now or abs(effective-timestamp(p['result_at']))>1:raise ValueError('Result time mismatch')
                    race=c.execute("SELECT body FROM events WHERE race_id=? AND kind='race' AND known_at<=? ORDER BY known_at DESC,id DESC LIMIT 1",(r.race_id,now)).fetchone()
                    if not race or timestamp(p['result_at'])<timestamp(json.loads(race['body'])['start']):raise ValueError('Result requires an existing race and a time after its start')
                if r.kind=='stats' and timestamp(p['period_end'])>effective:raise ValueError('Statistics period ends after its publication')
                h=digest([data.source_id,r.kind,r.race_id,r.effective_at,p])
                cur=c.execute('INSERT OR IGNORE INTO events(source_id,kind,race_id,effective_at,known_at,body,hash) VALUES(?,?,?,?,?,?,?)',(data.source_id,r.kind,r.race_id,effective,now,packed(p),h));added+=cur.rowcount
        return {'added':added,'duplicates':len(data.records)-added,'known_at':now,'historical_backdating':False}
    def events(self,race_id,kind,as_of=None,training=False):
        cut=self.clock() if as_of is None else as_of
        with self.connect() as c:
            rows=c.execute('SELECT * FROM events WHERE race_id=? AND kind=? AND known_at<=? AND effective_at<=? ORDER BY effective_at DESC,known_at DESC,id DESC',(race_id,kind,cut,cut)).fetchall()
        allowed={sid for sid in {r['source_id'] for r in rows} if self.permitted(sid,'training' if training else 'analysis')}
        return [dict(r)|{'payload':json.loads(r['body'])} for r in rows if r['source_id'] in allowed]
    def latest(self,race_id,kind,as_of=None,training=False):
        rows=self.events(race_id,kind,as_of,training)
        return rows[0] if rows else None
    def race_ids(self):
        with self.connect() as c:return [r[0] for r in c.execute("SELECT DISTINCT race_id FROM events WHERE kind='race' ORDER BY race_id")]
    def global_events(self,kind,as_of,training=False):
        with self.connect() as c:rows=c.execute('SELECT * FROM events WHERE kind=? AND known_at<=? AND effective_at<=? ORDER BY effective_at DESC,known_at DESC,id DESC',(kind,as_of,as_of)).fetchall()
        allowed={sid for sid in {r['source_id'] for r in rows} if self.permitted(sid,'training' if training else 'analysis')}
        return [dict(r)|{'payload':json.loads(r['body'])} for r in rows if r['source_id'] in allowed]
    def snapshot(self,race_id,as_of=None,training=False):
        cut=self.clock() if as_of is None else as_of
        race=self.latest(race_id,'race',cut,training)
        if not race:return None
        roster=self.latest(race_id,'roster',cut,training);lineup=self.latest(race_id,'lineup',cut,training)
        entries=roster['payload']['entries'] if roster else []
        lines=lineup['payload']['lines'] if lineup else []
        ids={e['id'] for e in entries}
        if any(x not in ids for g in lines for x in g):lines=[]
        return {'id':race_id,**race['payload'],'entries':entries,'lines':lines,'as_of':cut,'provenance':{'race':race['id'],'roster':roster['id'] if roster else None,'lineup':lineup['id'] if lineup and lines else None},'source_ids':sorted({e['source_id'] for e in [race,roster,lineup] if e})}
    def add_note(self,note_id,data):
        with self.connect() as c:
            row=c.execute('SELECT MAX(revision) FROM note_revisions WHERE note_id=?',(note_id,)).fetchone()
            revision=(row[0] or 0)+1;body=data.model_dump();h=digest(body)
            c.execute('INSERT INTO note_revisions(note_id,revision,body,hash,known_at) VALUES(?,?,?,?,?)',(note_id,revision,packed(body),h,self.clock()))
        return {'id':note_id,'revision':revision,'content_sha256':h,'verification':'unreviewed',**body}
    def review_note(self,note_id,data):
        with self.connect() as c:
            r=c.execute('SELECT * FROM note_revisions WHERE note_id=? ORDER BY revision DESC LIMIT 1',(note_id,)).fetchone()
            if not r or r['revision']!=data.revision or r['hash']!=data.content_sha256:raise ValueError('Review does not match the current note revision')
            note=json.loads(r['body'])
            if not note['evidence_reference'] or not note['evidence_excerpt'] or data.reviewed_evidence_reference!=note['evidence_reference']:raise ValueError('Matching evidence and excerpt are required')
            c.execute('INSERT INTO reviews(note_id,revision,hash,body,known_at) VALUES(?,?,?,?,?)',(note_id,r['revision'],r['hash'],packed(data.model_dump()),self.clock()))
        return {'verification':'operator_reviewed','independently_verified':False}
    def notes(self,players,as_of):
        out=[]
        with self.connect() as c:
            rows=c.execute('SELECT * FROM note_revisions WHERE known_at<=? ORDER BY revision DESC',(as_of,)).fetchall();seen=set()
            for r in rows:
                if r['note_id'] in seen:continue
                seen.add(r['note_id']);n=json.loads(r['body'])
                if n['player_id'] not in players:continue
                review=c.execute('SELECT * FROM reviews WHERE note_id=? AND revision=? AND hash=? AND known_at<=? ORDER BY id DESC LIMIT 1',(r['note_id'],r['revision'],r['hash'],as_of)).fetchone()
                out.append({'id':r['note_id'],'revision':r['revision'],'content_sha256':r['hash'],**n,'verification':'operator_reviewed' if review else 'unreviewed','review':json.loads(review['body']) if review else None})
        return out
