"""Series questions and bounded reading of Bangumi's directed subject relations."""

from __future__ import annotations

import asyncio
import re
import unicodedata
from urllib.parse import urlsplit

from .web_search import valid_source_url


_ORDER = re.compile(
    r"观看顺序|观影顺序|阅读顺序|补番顺序|追番顺序|入坑顺序|入坑指南|系列顺序"
    r"|(?:系列|正传|外传|番外|剧场版|动画|番剧|漫画).{0,16}(?:怎么入[坑手]|如何入[坑手]|从哪.{0,3}(?:看|读)|先看|先读|顺序|能跳|要跳|跳过)"
    r"|先看哪(?:一)?[部季篇]|先读哪(?:一)?[部卷]|(?:外传|番外|剧场版).{0,10}(?:必看|要看|单独看)"
)
_CHAPTER = re.compile(
    r"(?:动画|动漫|番剧|看完).{0,38}(?:接|从哪|对应|改编到|看到).{0,22}(?:漫画|原作|[话話章卷巻])"
    r"|(?:动画|动漫|番剧).{0,26}(?:漫画|原作).{0,14}(?:第几|哪一?)[话話章卷巻]"
    r"|(?:动画|动漫|番剧).{0,15}(?:看完|完结).{0,18}(?:漫画|原作).{0,8}从哪(?:里|儿)?接"
)
_PREVIOUS = {'前传', '前傳', '前作'}
_NEXT = {'续集', '續集', '续作', '續作'}
_BRANCH = {'番外篇', '外传', '外傳', '主线故事', '主線故事', '不同演绎', '不同演繹',
           '不同版本', '总集篇', '總集篇', '重制', '重製'}
_ADAPTATION = {'原作', '改编', '改編', '动画', '動畫', '漫画', '漫畫', '书籍', '書籍',
               '游戏', '遊戲', '小说', '小說', '单行本', '單行本', '三次元'}
_CHAPTER_HOSTS = {'shonenjumpplus.com', 'www.shonenjump.com', 'www.shueisha.co.jp',
                  'pocket.shonenmagazine.com', 'www.shogakukan.co.jp', 'comic-walker.com'}
_FROM_CHAPTER = re.compile(
    r"(?:动画|动漫|アニメ).{0,25}(?:看完|完结|结束|最終回|続き|后续|之后|之后的故事)"
    r".{0,24}(?:漫画|原作|コミック).{0,12}(?:从|起自|由|は)?\s*第?\s*\d{1,4}\s*[话話章卷巻]"
    r"|(?:动画|アニメ).{0,12}(?:改编到|改編到|对应|对应到|结束于|描いたのは).{0,12}(?:原作|漫画|コミック)\s*第?\s*\d{1,4}\s*[话話章卷巻]"
)


def series_request(query):
    return bool(_ORDER.search(query) or _CHAPTER.search(query))


def wants_chapter_mapping(query):
    return bool(_CHAPTER.search(query))


def series_title(query):
    """When there are no quotation marks, take only the explicit title before the request."""
    query = re.sub(r'^(?:请问|请|帮我|介绍一下|想问一下|我想知道|请介绍一下|想看|我想补|我想看|想补)\s*', '', query.strip())
    after_watching = re.match(r'^(?:刚刚?|已经)?看完(?:了)?(.{1,80}?)(?:的)?(?:动画|动漫|番剧|[，,])', query)
    if after_watching:
        query = after_watching[1].strip() + '动画看完'
    match = re.search(r'(?:这个|这个作品的|的)?(?:系列|观看顺序|观影顺序|阅读顺序|补番顺序|入坑顺序|动画看完|动漫看完|番剧看完|动画对应|先看哪|外传|番外)', query)
    if not match:
        return ''
    title = query[:match.start()].strip('的 ，,。？?《》「」')
    if title in {'', '这', '这个', '这部', '那个', '整部', '该', '这个动画', '看完', '看完了'} or len(title) > 80:
        return ''
    return title


def _work(row):
    identifier = row.get('id')
    name = row.get('name_cn') or row.get('name')
    if type(identifier) is not int or identifier <= 0 or not isinstance(name, str) or not name.strip():
        return None
    return {'id': identifier, 'title': ' '.join(name.split())[:110], 'url': f'https://bgm.tv/subject/{identifier}'}


async def series_guide(root, root_type, read_relations, deadline):
    nodes = {root['id']: {key: root[key] for key in ('id', 'title', 'url')}}
    frontier, visited = [root['id']], set()
    edges, side, adaptations = set(), {}, {}
    complete = True
    while frontier and len(visited) < 5:
        batch = frontier[:5 - len(visited)]
        visited.update(batch)
        tasks = [asyncio.create_task(read_relations(identifier)) for identifier in batch]
        try:
            done, _ = await asyncio.wait(tasks, timeout=max(0, deadline - asyncio.get_running_loop().time()))
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        for identifier, task in zip(batch, tasks):
            if task not in done or task.cancelled() or task.exception() is not None:
                complete = False
                continue
            rows = task.result()
            if not isinstance(rows, list):
                complete = False
                continue
            for row in rows:
                if not isinstance(row, dict) or not (work := _work(row)) or work['id'] == identifier:
                    continue
                label = row.get('relation')
                if not isinstance(label, str):
                    continue
                if row.get('type') == root_type and label in _PREVIOUS | _NEXT:
                    if len(nodes) >= 20 and work['id'] not in nodes:
                        complete = False
                        continue
                    nodes[work['id']] = work
                    edges.add((work['id'], identifier) if label in _PREVIOUS else (identifier, work['id']))
                elif label in _BRANCH or label in _ADAPTATION:
                    bucket = side if label in _BRANCH else adaptations
                    if len(bucket) < 12:
                        bucket[(identifier, work['id'], label)] = {
                            **work, 'relation': label, 'relative_to': nodes[identifier]['title'],
                        }
        frontier = [identifier for identifier in nodes if identifier not in visited]
        if asyncio.get_running_loop().time() >= deadline:
            break
    predecessors = {identifier: set() for identifier in nodes}
    for before, after in edges:
        predecessors[after].add(before)
    order = []
    while predecessors and edges:
        ready = [identifier for identifier, parents in predecessors.items() if not parents]
        if len(ready) != 1:
            order = []
            break
        identifier = ready[0]
        order.append(nodes[identifier])
        del predecessors[identifier]
        for parents in predecessors.values():
            parents.discard(identifier)
    return {'main_order': order, 'main_links': [{'before': nodes[a], 'after': nodes[b]} for a, b in sorted(edges)],
            'other_versions_and_side_stories': list(side.values()), 'adaptations_and_sources': list(adaptations.values()),
            'coverage': 'checked' if complete and not frontier else 'partial',
            'source_url': root['url'] + '/relations',
            'notice': '按已取得的 Bangumi 前传、续集关系整理主线；分支、不同版本和跨媒介条目单列。'}


def _publisher_url(url):
    if not valid_source_url(url):
        return False
    parts = urlsplit(url)
    return (parts.scheme == 'https' and parts.netloc == parts.hostname and parts.hostname in _CHAPTER_HOSTS
            and not re.search(r'(?:^|/)(?:comments?|forums?|users?|community)(?:/|$)', parts.path, re.I))


async def chapter_mapping(query, root, search_web, fetch_web):
    missing = {'status': 'unconfirmed', 'notice': '尚缺出版社明确说明动画与原作章节的对应，不能根据动画集数推算。'}
    if not search_web or not fetch_web:
        return missing
    title = root.get('original_title') or root['title']
    search = await search_web(f'"{title}" {query}')
    if not isinstance(search, dict) or not isinstance(search.get('results'), list):
        return missing
    urls = list(dict.fromkeys(row['url'] for row in search['results'] if isinstance(row, dict) and _publisher_url(row.get('url'))))[:2]
    pages = await asyncio.gather(*(fetch_web(url) for url in urls), return_exceptions=True)
    names = tuple(unicodedata.normalize('NFKC', value).casefold() for value in (title, root['title']) if value)
    for url, page in zip(urls, pages):
        if not isinstance(page, dict) or not _publisher_url(page.get('url') or url):
            continue
        page_title = page.get('title') if isinstance(page.get('title'), str) else ''
        content = page.get('content')
        if not isinstance(content, str):
            continue
        editorial = re.split(r'用户评论|读者评论|読者コメント|みんなのコメント|コメント一覧|投稿コメント|user comments', content[:12000], maxsplit=1, flags=re.I)[0]
        for line in re.split(r'[\n。！？]', unicodedata.normalize('NFKC', editorial)):
            match = _FROM_CHAPTER.search(line)
            if not match:
                continue
            lead = line[max(0, match.start() - 100):match.start()].strip()
            if not (any(lead.casefold().endswith(name + suffix) for name in names for suffix in ('', '》', '」', '』', 'の', '的'))
                    or not lead and any(unicodedata.normalize('NFKC', page_title).casefold() == name for name in names)):
                continue
            if re.search(r'猜|推测|不明|未定|可能|予想|[?？]', line):
                continue
            return {'status': 'publisher_statement', 'url': page.get('url') or url, 'excerpt': match.group()[:140]}
    return missing


def series_lines(guide):
    if not guide:
        return []
    lines = []
    if guide.get('main_order'):
        lines.append('已查到的主线顺序 · ' + ' → '.join(work['title'] for work in guide['main_order']))
    elif guide.get('main_links'):
        lines.append('目录存在多条或相互冲突的关系，以下按各条记录分别列出。')
        lines.extend(f"{edge['before']['title']} → {edge['after']['title']}" for edge in guide['main_links'][:6])
    else:
        lines.append('当前目录资料不足以确定主线先后。')
    for key in ('other_versions_and_side_stories', 'adaptations_and_sources'):
        for row in guide.get(key, [])[:4]:
            lines.append(f"{row['relative_to']} 的{row['relation']} · {row['title']} {row['url']}")
    if guide.get('coverage') == 'partial':
        lines.append('这里只列出本次查到的系列关系。')
    if mapping := guide.get('chapter_mapping'):
        lines.append(f"出版社说明 · {mapping['excerpt']} {mapping['url']}" if mapping.get('excerpt') else mapping['notice'])
    if guide.get('source_url'):
        lines.append('Bangumi 关联条目 ' + guide['source_url'])
    return lines
