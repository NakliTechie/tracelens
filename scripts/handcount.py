"""Independent hand count of turns, tool calls and errors per file; compare with the index.

Usage: TRACELENS_HOME=<home> python3 scripts/handcount.py <file-with-archive-relative-paths>
Plain json, no DuckDB, no code shared with the indexer.
"""
import json, sys, subprocess
import os
HOME=os.environ.get('TRACELENS_HOME', '.')
A=HOME+'/archive/'
for rel in open(sys.argv[1]).read().split():
    turns=calls=errs=human=0
    seen=set(); seen_ids=set()
    for raw in open(A+rel):
        if not raw.strip(): continue
        r=json.loads(raw)
        if rel.startswith('claude'):
            u=r.get('uuid')
            if u and u in seen: continue  # replayed after a compaction: count once
            if u: seen.add(u)
            t=r.get('type')
            if t in('user','assistant','system'): turns+=1
            if t=='system' and r.get('subtype')=='api_error': errs+=1
            if t=='assistant' and r.get('isApiErrorMessage'): errs+=1
            c=(r.get('message') or {}).get('content') if t in('user','assistant') else None
            if isinstance(c,list):
                for b in c:
                    if b.get('type')=='tool_use': calls+=1
                    if b.get('type')=='tool_result' and b.get('is_error'): errs+=1
        else:
            p=r.get('payload') or {}
            if r.get('type')=='event_msg' and p.get('type')=='turn_aborted': errs+=1
            if p.get('type')!='item_completed': continue
            it=p['item']; k=it['type']
            if k in('UserMessage','AgentMessage','Reasoning'): turns+=1
            elif k=='FileChange':
                calls+=len(it.get('changes',{}))
                if it.get('status')=='failed': errs+=len(it.get('changes',{}))
            elif k!='ContextCompaction':
                calls+=1
                if it.get('status')=='failed' or (it.get('exit_code') or 0)!=0: errs+=1
    db=subprocess.run(['duckdb','-readonly','-csv','-noheader',HOME+'/tracelens.duckdb',
      f"SELECT (SELECT count(*) FROM turns WHERE file='{rel}'), (SELECT count(*) FROM tool_calls WHERE file='{rel}'), (SELECT count(*) FROM errors WHERE file='{rel}')"],capture_output=True,text=True).stdout.strip()
    hand=f"{turns},{calls},{errs}"
    print(('MATCH ' if hand==db else 'DIFF  ')+f"hand={hand} db={db}  {rel}")
