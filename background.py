"""Ticket 121: the role's background library, injected a few paragraphs at a time.

Port of 小手机 (yecays-ai/veil) lib/chat/character-background.ts and the text matcher in
lib/memory/context.ts. The file is local (persona/<role>.background.md, not in the repo), split
on blank lines and re-read on every request. A paragraph may carry a `关键词：` / `Keywords:` line
(never sent) and a leading `[常驻]` marker (sent every request). Other paragraphs are sent only
when the current input or the last two rounds point at them. Deterministic, local, no model call.
"""
import os
import re
import unicodedata
from pathlib import Path

LIMITS = {'items': 3, 'characters': 1500, 'chunk': 700, 'chunks_per_paragraph': 2,
          'keywords': 30, 'keyword_characters': 40}
CONTEXT_LOG_KEEP = 500
NOTE = 'Parts of my background that matter for this moment (the rest of my background still holds):'

MARKER = re.compile(r'^\s*[\[【]\s*(?:常驻|core)\s*[\]】]\s*', re.I)
KEYWORD_LINE = re.compile(r'^[ \t]*(?:关键词|关键字|keywords?)[ \t]*[:：][ \t]*(.*)$', re.I | re.M)
LATIN = 'A-Za-z0-9À-ɏ'
WORD = re.compile(f'[{LATIN}]+')
HAN = re.compile('[㐀-䶿一-鿿豈-﫿\U00020000-\U0002fa1f]+')
LATIN_KEYWORD = re.compile(f"^[{LATIN}\\s'’.-]+$")
STOP_WORDS = set(
    'a an and are as at be but by for from had has have i in is it me my of on or our that the their '
    'them they this to was we were with you your she her him how do did does what when where about after '
    'any get got been lately better recently things thing today days feel feeling tell remember still come '
    'mind show would like should think before could will now some these those up well yet properly managed '
    'happening holding follow unsure more safe happy healthy calm fine'.split())
WEAK_WORDS = set(
    'adopt adopte grow grown keep take took leave leav left resign quit quitt care decide decided decision '
    'advice guidance plan ready rest going doing happening'.split())
CJK_COMMON = ('怎么样 我觉得 今天 明天 事情 觉得 应该 可以 什么 怎么 我们 你们 他们 她们 这个 那个 这样 那样 现在 '
              '最近 已经 之前 以后 然后 还是 一下 时候 是否 喜欢 知道 长得 好吗 了吗 的吗').split()
CJK_FUNCTIONS = re.compile('|'.join(sorted(CJK_COMMON, key=len, reverse=True)))
EMPLOYMENT_EXIT = re.compile(
    r'\b(?:resign(?:ed|ing)?|quit(?:ting)?|leav(?:e|ing)|left)\s+(?:from\s+)?'
    r'(?:(?:my|your|our|their|his|her|the|a|an|this|that)\s+)?'
    r'(?:(?!at\b|in\b|on\b|and\b|but\b|after\b|before\b|with\b|while\b)\w+\s+)?'
    r'(?:job|work|employment|employer|firm|company|position|committees?)\b')


def normalize(text):
    return unicodedata.normalize('NFKC', text).lower()


def normalize_keyword(keyword):
    return re.sub(r'\s+', ' ', normalize(keyword)).strip()


def parse_keywords(text):
    seen, result = set(), []
    for part in re.split(r'[,，、;；\n]+', text):
        keyword = part.strip()
        key = normalize_keyword(keyword)
        if key and key not in seen:
            seen.add(key)
            result.append(keyword)
    return result


def terms(text):
    normalized = normalize(text)
    words = {word for word in WORD.findall(normalized) if len(word) >= 3 and word not in STOP_WORDS}
    weak = set()
    for word in list(words):
        forms = [word]
        base = (word[:-3] if len(word) > 5 and word.endswith('ing')
                else word[:-2] if len(word) > 4 and word.endswith('ed') else None)
        if base and re.fullmatch('[a-z]+', base):
            forms += [base, base + 'e']
            if re.search(r'([bcdfghjklmnpqrtvwxyz])\1$', base):
                forms.append(base[:-1])
        if word == 'slept':
            forms.append('sleep')
        if word == 'taught':
            forms.append('teach')
        if any(form in STOP_WORDS for form in forms):
            words.discard(word)
        else:
            words.update(forms)
        if any(form in WEAK_WORDS for form in forms):
            weak.update(forms)
    words -= STOP_WORDS
    # Separate function phrases before cutting pairs, so removing 觉得 cannot leave a spurious 我觉.
    runs = HAN.findall(CJK_FUNCTIONS.sub(' ', normalized))
    bigrams = {run[i - 1] + run[i] for run in runs for i in range(1, len(run))}
    concepts = set()
    if EMPLOYMENT_EXIT.search(normalized) or re.search('辞职|离职|离开工作', normalized):
        concepts.add('employment-exit')
    return {'words': words, 'weak': weak, 'bigrams': bigrams, 'runs': runs, 'concepts': concepts}


def match(query, other):
    words = [word for word in query['words'] & other['words']
             if word not in query['weak'] and word not in other['weak']]
    pairs = query['bigrams'] & other['bigrams']
    short = [pair for pair in pairs
             if any(run.startswith(pair) or run.endswith(pair) for run in query['runs'])
             and any(run.startswith(pair) or run.endswith(pair) for run in other['runs'])]
    accepted = pairs if len(pairs) >= 2 else short
    return ({'w:' + word for word in words} | {'c:' + pair for pair in accepted}
            | {'s:' + concept for concept in query['concepts'] & other['concepts']})


def text_matcher(query):
    q = terms(query)
    return lambda text: len(match(q, terms(text)))


def parse_paragraph(raw):
    marked = bool(MARKER.match(raw))
    text = MARKER.sub('', raw, count=1) if marked else raw
    keywords = []
    for found in KEYWORD_LINE.finditer(text):
        keywords += parse_keywords(found.group(1))
    text = re.sub(r'\n{3,}', '\n\n', KEYWORD_LINE.sub('', text)).strip()
    seen, limited = set(), []
    for keyword in keywords:
        key = normalize_keyword(keyword)
        if key and len(key) <= LIMITS['keyword_characters'] and key not in seen:
            seen.add(key)
            limited.append(keyword)
    return marked, text, limited[:LIMITS['keywords']]


def split_background(text):
    """Return (core paragraphs, retrievable paragraphs). Numbering is 1-based paragraph order in the file."""
    core, retrievable = [], []
    raw = [part.strip() for part in re.split(r'\n[ \t]*\n+', text.replace('\r\n', '\n').replace('\r', '\n'))]
    for index, part in enumerate((part for part in raw if part), 1):
        marked, body, keywords = parse_paragraph(part)
        if not body:
            continue
        if marked:
            core.append({'index': index, 'text': body})
        else:
            retrievable.append({'index': index, 'text': body, 'keywords': keywords})
    return core, retrievable


def sentences(text):
    return [part for part in re.split(r'(?<=[。！？!?])|(?<=\.\s)|(?<=\n)', text) if part.strip()]


def chunk_paragraph(paragraph):
    """A paragraph that fits the budget is one chunk; a longer one is cut at sentence boundaries."""
    def make(text, part):
        return {'paragraph': paragraph['index'], 'part': part, 'text': text.strip(),
                'order': paragraph['index'] * 1000 + part}
    if len(paragraph['text']) <= LIMITS['characters']:
        return [make(paragraph['text'], 0)], 0
    chunks, current, skipped = [], '', 0
    for sentence in sentences(paragraph['text']):
        if len(sentence.strip()) > LIMITS['characters']:
            if current.strip():
                chunks.append(make(current, len(chunks)))
            current = ''
            skipped += len(sentence.strip())
            continue
        if current and len(current) + len(sentence) > LIMITS['chunk']:
            if current.strip():
                chunks.append(make(current, len(chunks)))
            current = ''
        current += sentence
    if current.strip():
        chunks.append(make(current, len(chunks)))
    return chunks, skipped


def keyword_hit(haystack, keyword):
    """ASCII keywords need word boundaries; CJK keywords match as substrings."""
    needle = normalize_keyword(keyword)
    if not needle:
        return False
    if not LATIN_KEYWORD.match(needle):
        return needle in haystack
    at = haystack.find(needle)
    while at != -1:
        before = haystack[at - 1] if at else ''
        after = haystack[at + len(needle)] if at + len(needle) < len(haystack) else ''
        if not before.isalnum() and not after.isalnum():
            return True
        at = haystack.find(needle, at + 1)
    return False


def select_background(retrievable, current, recent):
    """Pick chunks for this request. A keyword in the current input is the strongest signal; shared words
    with the current input come next; the last two rounds need a keyword or two shared words."""
    if not retrievable:
        return [], 0
    now_text, recent_text = normalize(current), normalize(recent)
    match_now, match_recent = text_matcher(current), text_matcher(recent)
    ranked, skipped_total = [], 0
    for paragraph in retrievable:
        chunks, skipped = chunk_paragraph(paragraph)
        skipped_total += skipped
        if not chunks:
            continue
        now_keywords = [k for k in paragraph['keywords'] if keyword_hit(now_text, k)]
        recent_keywords = [k for k in paragraph['keywords'] if keyword_hit(recent_text, k)]
        keyword_band = 3 if now_keywords else 1 if recent_keywords else 0
        keywords = now_keywords or recent_keywords
        own = []
        for chunk in chunks:
            now, before = match_now(chunk['text']), match_recent(chunk['text'])
            band = 2 if now > 0 else 1 if before >= 2 else 0
            own.append({'chunk': chunk, 'band': band, 'score': now * 2 + before, 'via': 'text', 'keywords': []})
        hits = [item for item in own if item['band'] > 0]
        if keyword_band == 0:
            picked = hits
        elif hits:
            picked = [{**item, 'band': max(item['band'], keyword_band), 'keywords': keywords,
                       'via': 'keyword+text' if keyword_band >= item['band'] else 'text'}
                      for item in hits]
        else:
            # Keyword fallback: only the opening chunk, so a keyword never drags a whole long paragraph in.
            picked = [{'chunk': chunks[0], 'band': keyword_band, 'score': 0, 'via': 'keyword', 'keywords': keywords}]
        picked.sort(key=lambda item: (-item['band'], -item['score'], item['chunk']['order']))
        ranked += picked[:LIMITS['chunks_per_paragraph']]
    ranked.sort(key=lambda item: (-item['band'], -item['score'], item['chunk']['order']))
    chosen, characters = [], 0
    for item in ranked:
        if len(chosen) == LIMITS['items']:
            break
        if any(other['chunk']['text'] == item['chunk']['text'] for other in chosen):
            continue
        added = len(item['chunk']['text']) + (2 if chosen else 0)
        if characters + added > LIMITS['characters']:
            continue
        chosen.append(item)
        characters += added
    chosen.sort(key=lambda item: item['chunk']['order'])
    return chosen, skipped_total


def message_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(part.get('text', '') for part in content
                         if isinstance(part, dict) and isinstance(part.get('text'), str))
    return ''


def recent_text(history):
    """The last two rounds (user and assistant messages), like 小手机's recent turns."""
    users = [index for index, message in enumerate(history) if message.get('role') == 'user']
    start = users[-2] if len(users) >= 2 else users[0] if users else len(history)
    return '\n'.join(message_text(message.get('content'))
                     for message in history[start:] if message.get('role') in ('user', 'assistant'))


def background_path(persona_path):
    persona_path = Path(persona_path)
    return persona_path.with_name(persona_path.stem + '.background.md')


def background_context(path, current, history):
    """Return (system message or None, log details). Missing or unreadable file means no background."""
    details = {'paragraphs': [], 'core': [], 'characters': 0}
    if not path:
        return None, details
    try:
        text = Path(path).read_text(encoding='utf-8-sig')
    except OSError:
        return None, details
    core, retrievable = split_background(text)
    chosen, skipped = select_background(retrievable, current, recent_text(history))
    entries = [item['text'] for item in core]
    previous = None
    for item in chosen:
        chunk = item['chunk']
        # Adjacent chunks of one paragraph read as one passage.
        if previous and previous['paragraph'] == chunk['paragraph'] and chunk['part'] == previous['part'] + 1:
            entries[-1] += '\n' + chunk['text']
        else:
            entries.append(chunk['text'])
        previous = chunk
        details['paragraphs'].append({'n': chunk['paragraph'], 'via': item['via'], 'keywords': item['keywords']})
    details['core'] = [item['index'] for item in core]
    if skipped:
        details['skipped_oversize_characters'] = skipped
    if not entries:
        return None, details
    content = '[background]\n' + NOTE + '\n' + '\n\n'.join(entries)
    details['characters'] = len(content)
    return {'role': 'system', 'content': content}, details


def append_context_log(store, character_id, row, keep=CONTEXT_LOG_KEEP):
    """Append one row to <role>/context-log.jsonl and keep only the newest `keep` rows."""
    name = f'{character_id}/context-log.jsonl'
    store.append(name, row)
    path = store.root / name
    with store.mutex:
        lines = path.read_bytes().splitlines(keepends=True)
        if len(lines) <= keep + keep // 5:
            return
        temporary = path.with_suffix('.tmp')
        data = b''.join(lines[-keep:])
        with temporary.open('wb', buffering=0) as handle:
            handle.write(data)
            os.fsync(handle.fileno())
        os.replace(temporary, path)


def request_kind(chat, text, autonomy):
    if autonomy:
        if getattr(chat, 'codex_watch_mode', False):
            return 'codex'
        return 'glance' if getattr(chat, 'glance_mode', False) else 'autonomy'
    return 'qq' if isinstance(text, str) and text.startswith('[Lan sent this on QQ') else 'chat'


def context_chars(messages):
    return sum(len(message_text(message.get('content'))) for message in messages)
