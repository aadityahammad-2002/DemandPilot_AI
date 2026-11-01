"""Extract USER-authored text prompts for selected conversations from a local ChatGPT export.

No assistant messages or whole chat transcripts are written. This is a local-only
helper; candidate must review the result for credentials/personal content before
committing. Export formats can change; unsupported message content is omitted.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path


def timestamp(value):
    if value is None: return 'unknown'
    try: return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError): return 'unknown'


def extract_text(message):
    content = message.get('content') or {}
    parts = content.get('parts') or []
    texts = [s for s in parts if isinstance(s, str) and s.strip()]
    if content.get('content_type') == 'text' and not texts:
        text = content.get('text')
        if isinstance(text, str) and text.strip(): texts.append(text)
    return '\n\n'.join(texts).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', type=Path, required=True, help='local ChatGPT export conversations.json')
    ap.add_argument('--output', type=Path, required=True, help='output markdown path outside public repo until reviewed')
    ap.add_argument('--title-contains', default='YottaFlex', help='case-insensitive filter for relevant conversation titles')
    args = ap.parse_args()
    if args.output.exists(): raise SystemExit('Refusing to overwrite existing prompt log')
    data = json.loads(args.source.read_text(encoding='utf-8'))
    if not isinstance(data, list): raise SystemExit('Unexpected ChatGPT export shape')
    matches=[]
    for conv in data:
        title=str(conv.get('title') or '')
        if args.title_contains.lower() not in title.lower(): continue
        found=[]
        for node in (conv.get('mapping') or {}).values():
            msg=node.get('message') or {}
            if (msg.get('author') or {}).get('role') != 'user': continue
            body=extract_text(msg)
            if not body: continue
            found.append((float(msg.get('create_time') or 0),str(msg.get('id') or ''),body))
        found.sort(key=lambda x:(x[0],x[1]))
        if found: matches.append((title,found))
    out=['# Exact user prompts from selected ChatGPT conversations', '',
         '> Extracted locally from an export; user-authored text only, without model responses.',
         '> REVIEW AND REMOVE SECRETS/PRIVATE CONTENT BEFORE PUBLISHING.']
    for title, msgs in matches:
        out.extend(['',f'## Conversation: {title}'])
        for i,(ts,_,body) in enumerate(msgs,1):
            out.extend(['',f'### Prompt {i} - {timestamp(ts)}','',body])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text('\n'.join(out)+'\n',encoding='utf-8')
    print(f'Extracted {sum(len(x[1]) for x in matches)} text prompts across {len(matches)} matching conversations.')
    print(f'Output: {args.output}')
    if not matches: print('No matching titles; check --title-contains and export format.')


if __name__=='__main__':main()
