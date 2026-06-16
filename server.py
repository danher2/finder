"""
Opportunity Finder - Backend Server
Scans Reddit, YouTube, and Tavily for pain points and business gaps.
Port: 8766 (different from news analyzer on 8765)
"""

import json
import re
import time
import threading
import urllib.request
import urllib.parse
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

PORT = 8766

TAVILY_API_URL = "https://api.tavily.com/search"

# ── HELPERS ───────────────────────────────────────────────────────────────────
def fetch_url(url, timeout=12, headers=None):
    default_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/html, */*',
        'Accept-Language': 'en-US,en;q=0.9',
    }
    if headers:
        default_headers.update(headers)
    try:
        req = urllib.request.Request(url, headers=default_headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            charset = 'utf-8'
            ct = resp.headers.get('Content-Type', '')
            m = re.search(r'charset=([^\s;]+)', ct)
            if m:
                charset = m.group(1).strip('"\'')
            return resp.read().decode(charset, errors='replace')
    except Exception as e:
        print(f'  [fetch] Error {url[:60]}: {e}')
        return None

# ── REDDIT API ────────────────────────────────────────────────────────────────
def get_reddit_token(client_id, client_secret):
    """Get OAuth token from Reddit."""
    try:
        credentials = f"{client_id}:{client_secret}"
        import base64
        encoded = base64.b64encode(credentials.encode()).decode()
        data = urllib.parse.urlencode({'grant_type': 'client_credentials'}).encode()
        req = urllib.request.Request(
            'https://www.reddit.com/api/v1/access_token',
            data=data,
            headers={
                'Authorization': f'Basic {encoded}',
                'User-Agent': 'OpportunityFinder/1.0',
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return data.get('access_token')
    except Exception as e:
        print(f'  [Reddit] Token error: {e}')
        return None

def search_reddit(niche, client_id, client_secret, limit=25):
    """Search Reddit for posts about the niche."""
    token = get_reddit_token(client_id, client_secret)
    if not token:
        return []

    results = []
    queries = [
        f"{niche} problem",
        f"{niche} frustrated",
        f"{niche} wish there was",
        f"{niche} anyone else struggling",
    ]

    for query in queries[:2]:  # limit to 2 queries to save time
        try:
            encoded = urllib.parse.quote_plus(query)
            url = f"https://oauth.reddit.com/search?q={encoded}&sort=relevance&limit={limit}&type=link,comment"
            html = fetch_url(url, headers={
                'Authorization': f'Bearer {token}',
                'User-Agent': 'OpportunityFinder/1.0',
            })
            if not html:
                continue
            data = json.loads(html)
            posts = data.get('data', {}).get('children', [])
            for post in posts:
                p = post.get('data', {})
                if p.get('selftext') and len(p.get('selftext', '')) > 50:
                    results.append({
                        'platform': 'reddit',
                        'title': p.get('title', ''),
                        'text': p.get('selftext', '')[:1000],
                        'url': f"https://reddit.com{p.get('permalink', '')}",
                        'score': p.get('score', 0),
                        'subreddit': p.get('subreddit', ''),
                        'comments': p.get('num_comments', 0),
                    })
        except Exception as e:
            print(f'  [Reddit] Search error: {e}')

    print(f'  [Reddit] Found {len(results)} posts for "{niche}"')
    return results[:30]

# ── YOUTUBE API ───────────────────────────────────────────────────────────────
def search_youtube(niche, api_key, max_results=10):
    """Search YouTube for videos about the niche and get comments."""
    results = []
    try:
        # Search for videos
        encoded = urllib.parse.quote_plus(f"{niche} problems issues review")
        url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&q={encoded}&type=video&maxResults={max_results}&order=relevance&key={api_key}"
        html = fetch_url(url)
        if not html:
            return []

        data = json.loads(html)
        videos = data.get('items', [])

        # Get comments for top 3 videos
        for video in videos[:3]:
            video_id = video['id'].get('videoId')
            if not video_id:
                continue

            title = video['snippet'].get('title', '')
            try:
                comments_url = f"https://www.googleapis.com/youtube/v3/commentThreads?part=snippet&videoId={video_id}&maxResults=20&order=relevance&key={api_key}"
                comments_html = fetch_url(comments_url)
                if not comments_html:
                    continue

                comments_data = json.loads(comments_html)
                for item in comments_data.get('items', []):
                    comment = item['snippet']['topLevelComment']['snippet']
                    text = comment.get('textDisplay', '')
                    if len(text) > 30:
                        results.append({
                            'platform': 'youtube',
                            'title': f"Comment on: {title}",
                            'text': text[:800],
                            'url': f"https://youtube.com/watch?v={video_id}",
                            'score': comment.get('likeCount', 0),
                            'video_title': title,
                        })
            except Exception as e:
                print(f'  [YouTube] Comments error for {video_id}: {e}')

    except Exception as e:
        print(f'  [YouTube] Search error: {e}')

    print(f'  [YouTube] Found {len(results)} comments for "{niche}"')
    return results[:30]

# ── TAVILY SEARCH ─────────────────────────────────────────────────────────────
def search_tavily(query, api_key, num=6):
    try:
        payload = json.dumps({
            'query': query,
            'api_key': api_key,
            'search_depth': 'advanced',
            'max_results': num,
            'include_raw_content': True,
            'include_answer': False,
        }).encode('utf-8')
        req = urllib.request.Request(
            TAVILY_API_URL,
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode('utf-8'))

        results = []
        for r in data.get('results', []):
            if r.get('url') and r.get('title'):
                full_text = r.get('raw_content') or r.get('content') or ''
                results.append({
                    'platform': 'web',
                    'title': r.get('title', ''),
                    'text': (full_text[:1500] if full_text else r.get('content', '')),
                    'url': r['url'],
                    'score': r.get('score', 0),
                    'full_text': full_text[:6000],
                })
        print(f'  [Tavily] "{query[:40]}" → {len(results)} results')
        return results
    except Exception as e:
        print(f'  [Tavily] Error: {e}')
        return []

def multi_search_tavily(queries, api_key, num_per_query=5):
    all_results = []
    seen_urls = set()
    lock = threading.Lock()

    def run_query(q):
        results = search_tavily(q, api_key, num_per_query)
        with lock:
            for r in results:
                if r['url'] not in seen_urls:
                    seen_urls.add(r['url'])
                    all_results.append(r)

    threads = [threading.Thread(target=run_query, args=(q,)) for q in queries]
    for t in threads:
        t.daemon = True
        t.start()
    for t in threads:
        t.join(timeout=25)

    all_results.sort(key=lambda x: x.get('score', 0), reverse=True)
    return all_results

# ── HTTP SERVER ───────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        print(f'[{self.address_string()}] {format % args}')

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', len(body))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, x-api-key, Authorization, x-groq-key')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, x-api-key, Authorization, x-groq-key')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.end_headers()

    def read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def do_GET(self):
        if self.path == '/health':
            self.send_json({'status': 'ok', 'port': PORT})
        elif self.path in ('/', '/app', '/app.html'):
            import os
            html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'app.html')
            try:
                with open(html_path, 'rb') as f:
                    content = f.read()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', len(content))
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(content)
            except FileNotFoundError:
                self.send_json({'error': 'app.html not found'}, 404)
        else:
            self.send_json({'error': 'not found'}, 404)

    def do_POST(self):
        path = urlparse(self.path).path

        # ── SCAN NICHE ────────────────────────────────────────────────────────
        if path == '/scan':
            body = self.read_body()
            niche = body.get('niche', '').strip()
            platforms = body.get('platforms', ['reddit', 'youtube', 'web'])
            time_range = body.get('time_range', '30')
            reddit_id = body.get('reddit_client_id', '')
            reddit_secret = body.get('reddit_client_secret', '')
            youtube_key = body.get('youtube_key', '')
            tavily_key = body.get('tavily_key', '')

            if not niche:
                return self.send_json({'error': 'niche required'}, 400)

            print(f'\n🎯 Scanning niche: "{niche}" | platforms: {platforms}')
            all_data = []

            # Reddit
            if 'reddit' in platforms and reddit_id and reddit_secret:
                reddit_data = search_reddit(niche, reddit_id, reddit_secret)
                all_data.extend(reddit_data)
            elif 'reddit' in platforms:
                print('  [Reddit] Skipped — no credentials')

            # YouTube
            if 'youtube' in platforms and youtube_key:
                yt_data = search_youtube(niche, youtube_key)
                all_data.extend(yt_data)
            elif 'youtube' in platforms:
                print('  [YouTube] Skipped — no API key')

            # Tavily web search
            if 'web' in platforms and tavily_key:
                queries = [
                    f"{niche} problems complaints frustrated",
                    f"{niche} market gaps underserved",
                    f"{niche} competitors reviews",
                    f"best {niche} tools alternatives",
                ]
                web_data = multi_search_tavily(queries, tavily_key, num_per_query=4)
                all_data.extend(web_data)
            elif 'web' in platforms:
                print('  [Tavily] Skipped — no API key')

            print(f'  Total raw data: {len(all_data)} items')
            self.send_json({
                'niche': niche,
                'raw_data': all_data,
                'counts': {
                    'reddit': len([x for x in all_data if x.get('platform') == 'reddit']),
                    'youtube': len([x for x in all_data if x.get('platform') == 'youtube']),
                    'web': len([x for x in all_data if x.get('platform') == 'web']),
                    'total': len(all_data),
                }
            })

        # ── TAVILY CHAT SEARCH ────────────────────────────────────────────────
        elif path == '/search':
            body = self.read_body()
            query = body.get('query', '')
            tavily_key = body.get('tavily_key', '')

            if not query or not tavily_key:
                return self.send_json({'scraped': [], 'total_found': 0, 'queries': []})

            results = search_tavily(query, tavily_key, num=5)
            self.send_json({
                'queries': [query],
                'total_found': len(results),
                'scraped': results,
            })

        # ── OPENROUTER PROXY ──────────────────────────────────────────────────
        elif path == '/openrouter':
            body_raw_len = int(self.headers.get('Content-Length', 0))
            body_raw = self.rfile.read(body_raw_len)
            try:
                body_obj = json.loads(body_raw)
            except Exception:
                body_obj = {}

            api_key = body_obj.pop('api_key', '') or self.headers.get('Authorization', '').replace('Bearer ', '')
            print(f'[OpenRouter] Key: {api_key[:12] + "..." if api_key else "MISSING"} | Model: {body_obj.get("model", "?")}')

            if not api_key:
                return self.send_json({'error': {'message': 'No OpenRouter API key. Open Settings.', 'code': 401}}, 401)

            clean_body = json.dumps(body_obj, ensure_ascii=False).encode('utf-8')
            try:
                req = urllib.request.Request(
                    'https://openrouter.ai/api/v1/chat/completions',
                    data=clean_body,
                    headers={
                        'Content-Type': 'application/json',
                        'Authorization': f'Bearer {api_key}',
                        'HTTP-Referer': 'http://localhost:8766',
                        'X-Title': 'Opportunity Finder',
                    },
                    method='POST'
                )
                with urllib.request.urlopen(req, timeout=90) as resp:
                    result = json.loads(resp.read().decode('utf-8'))
                self.send_json(result)
            except urllib.error.HTTPError as e:
                err_body = e.read().decode('utf-8')
                print(f'[OpenRouter] HTTP {e.code}: {err_body[:200]}')
                try:
                    self.send_json({'error': json.loads(err_body)}, e.code)
                except Exception:
                    self.send_json({'error': err_body}, e.code)
            except Exception as e:
                print(f'[OpenRouter] Exception: {e}')
                self.send_json({'error': str(e)}, 500)

        # ── ANTHROPIC PROXY ───────────────────────────────────────────────────
        elif path == '/anthropic':
            body_raw_len = int(self.headers.get('Content-Length', 0))
            body_raw = self.rfile.read(body_raw_len)
            api_key = self.headers.get('x-api-key', '')
            try:
                req = urllib.request.Request(
                    'https://api.anthropic.com/v1/messages',
                    data=body_raw,
                    headers={
                        'Content-Type': 'application/json',
                        'x-api-key': api_key,
                        'anthropic-version': '2023-06-01',
                    },
                    method='POST'
                )
                with urllib.request.urlopen(req, timeout=90) as resp:
                    result = json.loads(resp.read().decode('utf-8'))
                self.send_json(result)
            except urllib.error.HTTPError as e:
                self.send_json({'error': e.read().decode('utf-8')}, e.code)
            except Exception as e:
                self.send_json({'error': str(e)}, 500)

        else:
            self.send_json({'error': 'not found'}, 404)


# ── MAIN ──────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print('=' * 60)
    print('  OPPORTUNITY FINDER — Backend Server')
    print(f'  Running on http://localhost:{PORT}')
    print('  Data: Reddit API + YouTube API + Tavily Web Search')
    print('  Keep this window open while using the app.')
    print('=' * 60)
    server = HTTPServer(('localhost', PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nServer stopped.')
