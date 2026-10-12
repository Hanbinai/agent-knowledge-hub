"""Disposable local FTS5 index of catalogued skill/lesson bodies, never executable."""
from __future__ import annotations
import hashlib
import json
import os
import re
import sqlite3
import uuid
from pathlib import Path
import core

def fingerprint(root):
    return hashlib.sha256((root/'90_Assets/external-resources/catalog.json').read_bytes()).hexdigest()

def rebuild(root: Path) -> dict:
    from external_resources import contained, digest
    catalog_path=root/'90_Assets/external-resources/catalog.json'
    raw=catalog_path.read_bytes(); entries=json.loads(raw)['resources']
    target=root/'.state/external-resource-search.db'
    target.parent.mkdir(parents=True,exist_ok=True)
    temp=target.with_name(target.name+'.building-'+uuid.uuid4().hex)
    with core.FileLock(root/'.state/external-resource-search.lock',timeout=30,stale_after=3600):
        conn=sqlite3.connect(temp)
        try:
            conn.execute('PRAGMA journal_mode=MEMORY')
            conn.execute("CREATE VIRTUAL TABLE docs USING fts5(id UNINDEXED,title,description,body,tokenize='trigram')")
            conn.execute('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)')
            conn.execute('CREATE TABLE sources(id TEXT PRIMARY KEY,path TEXT,sha256 TEXT,size INTEGER,mtime INTEGER)')
            count=0
            for e in entries:
                if e['kind'] not in ('skill','lesson'):continue
                p=contained(root,e['local_path']); data=p.read_bytes()
                if digest(data)!=e['sha256']:raise ValueError('Modified source: '+e['id'])
                stat=p.stat()
                conn.execute('INSERT INTO docs VALUES(?,?,?,?)',(e['id'],e['title'],e['description']+' '+e.get('keywords',''),data.decode('utf-8')))
                conn.execute('INSERT INTO sources VALUES(?,?,?,?,?)',(e['id'],e['local_path'],e['sha256'],stat.st_size,stat.st_mtime_ns))
                count+=1
            if hashlib.sha256(raw).hexdigest()!=fingerprint(root):raise ValueError('Catalog changed during rebuild')
            conn.executemany('INSERT INTO meta VALUES(?,?)',[('catalog_sha256',fingerprint(root)),('schema','1'),('built_at',core.now_iso()),('records',str(count))])
            conn.commit()
        finally:conn.close()
        os.replace(temp,target)
    return {'indexed_bodies':count,'path':str(target),'coverage':'catalogued SKILL.md and lesson docs/en.md only; no scripts executed','rebuildable':True}

def search(root: Path,query: str,visible: dict,limit=50):
    from external_resources import contained,digest
    path=root/'.state/external-resource-search.db'
    if not path.exists():return [],'missing'
    conn=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
    conn.row_factory=sqlite3.Row
    try:
        meta=dict(conn.execute('SELECT key,value FROM meta'))
        if meta.get('catalog_sha256')!=fingerprint(root):return [],'stale_catalog'
        terms=re.findall(r'[\w-]+',query.casefold())[:16]
        if not terms:return [],'ready'
        # Quote every FTS token: user text cannot become a query operator.
        long=[t for t in terms if len(t)>=3]
        if not long:return [],'query_too_short_for_trigram'
        expression='body : ('+' OR '.join('"'+t.replace('"','""')+'"' for t in long)+')'
        # Fetch all matches before ACL filtering, so denied rows cannot hide allowed results.
        matches=conn.execute("SELECT id,bm25(docs) rank,snippet(docs,3,'[',']',' … ',28) excerpt FROM docs WHERE docs MATCH ? ORDER BY rank,id",(expression,)).fetchall()
        rows=[]; stale=False
        for item in matches:
            e=visible.get(item['id'])
            if not e:continue
            data=contained(root,e['local_path']).read_bytes()
            if digest(data)!=e['sha256']:
                stale=True;continue
            body=data.decode('utf-8')
            # Trigram snippets can cut a highlighted word in half. Use the verified
            # source for a complete, auditable context window instead.
            matched=next((m for t in long if (m:=re.search(re.escape(t),body,re.IGNORECASE))),None)
            if matched is None:continue
            begin=max(0,matched.start()-100); end=min(len(body),matched.end()+180)
            excerpt=('… ' if begin else '')+body[begin:end]+(' …' if end<len(body) else '')
            rows.append({'id':item['id'],'excerpt':excerpt,'source_path':e['source_path'],'sha256':e['sha256'],'rank':item['rank']})
            if len(rows)>=limit:break
        return rows,('stale_source_excluded' if stale else 'ready')
    finally:conn.close()
