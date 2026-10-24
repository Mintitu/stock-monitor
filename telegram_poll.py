#!/usr/bin/env python3
"""Poll Telegram for /raport and dispatch stock-all.yml through GitHub API."""
import os, sys, requests
TOKEN=os.getenv('TELEGRAM_BOT_TOKEN','').strip(); CHAT=os.getenv('TELEGRAM_CHAT_ID','').strip(); PAT=os.getenv('GH_PAT','').strip(); REPO=os.getenv('GITHUB_REPOSITORY','').strip(); BRANCH=os.getenv('DEFAULT_BRANCH','').strip() or 'main'; OFFSET_FILE='.telegram-offset'
if not all([TOKEN,CHAT,PAT,REPO]): raise SystemExit('Missing TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, GH_PAT, or repository context.')
try: offset=int(open(OFFSET_FILE,encoding='utf-8').read().strip())
except (OSError,ValueError): offset=0
api=f'https://api.telegram.org/bot{TOKEN}'
r=requests.get(api+'/getUpdates',params={'offset':offset,'timeout':0,'allowed_updates':'["message"]'},timeout=20); r.raise_for_status(); body=r.json()
if not body.get('ok'): raise SystemExit('Telegram getUpdates failed; check whether a webhook is configured.')
updates=body.get('result',[])
if not updates: print('No new Telegram messages.'); raise SystemExit(0)
max_id=offset-1; commands=[]
for up in updates:
    max_id=max(max_id,int(up.get('update_id',-1))); msg=up.get('message') or {}; txt=(msg.get('text') or '').strip(); chat=str((msg.get('chat') or {}).get('id',''))
    if chat==str(CHAT) and txt and txt.split()[0].split('@')[0].lower()=='/raport': commands.append(up)
if commands:
    url=f'https://api.github.com/repos/{REPO}/actions/workflows/stock-all.yml/dispatches'
    d=requests.post(url,headers={'Authorization':f'Bearer {PAT}','Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'},json={'ref':BRANCH},timeout=30)
    if d.status_code not in (201,204): print(f'GitHub dispatch failed: HTTP {d.status_code}'); raise SystemExit(1)
    a=requests.post(api+'/sendMessage',data={'chat_id':CHAT,'text':'Am primit /raport. Pornesc scanarea Nasdaq + NYSE; raportul va sosi când analiza se termină.'},timeout=20); a.raise_for_status(); print(f'Dispatched scanner for {len(commands)} command(s).')
else: print(f'Read {len(updates)} update(s); no /raport command from configured chat.')
with open(OFFSET_FILE,'w',encoding='utf-8') as f: f.write(str(max_id+1))
