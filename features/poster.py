import asyncio
import aiohttp
from bs4 import BeautifulSoup
import logging
import re
from config import Config

logger = logging.getLogger(__name__)

async def _find_poster_from_imdb(query: str):
    """Finds a poster and its unique IMDb ID (e.g., tt12345)."""
    try:
        search_url = f"https://www.imdb.com/find?q={re.sub(r'\s+', '+', query)}"
        headers = {'User-Agent': 'Mozilla/5.0', 'Accept-Language': 'en-US,en;q=0.5'}
        async with aiohttp.ClientSession(headers=headers) as s:
            async with s.get(search_url, timeout=10) as resp:
                if resp.status != 200: return None, None
                soup = BeautifulSoup(await resp.text(), 'html.parser')
                result = soup.select_one("a.ipc-metadata-list-summary-item__t")
                if not result or not result.get('href'): return None, None
                
                imdb_id_match = re.search(r'/title/(tt\d+)/', result['href'])
                if not imdb_id_match: return None, None
                imdb_id = imdb_id_match.group(1)
                
                movie_url = f"https://www.imdb.com/title/{imdb_id}/"
                async with s.get(movie_url, timeout=10) as movie_resp:
                    if movie_resp.status != 200: return None, None
                    movie_soup = BeautifulSoup(await movie_resp.text(), 'html.parser')
                    img = movie_soup.select_one('div[data-testid="hero-media__poster"] img.ipc-image')
                    if img and img.get('src'):
                        poster_url = img['src'].split('_V1_')[0] + "_V1_FMjpg_UX1000_.jpg"
                        return poster_url, f"imdb-{imdb_id}"
    except Exception: pass
    return None, None

async def _find_poster_from_tmdb(query: str, year: str = None):
    """Finds a poster and its unique TMDB ID (e.g., tv-12345 or movie-12345)."""
    if not Config.TMDB_API_KEY: return None, None
    try:
        params = {"api_key": Config.TMDB_API_KEY, "query": query, "include_adult": "false"}
        if year: params['year'] = year
        async with aiohttp.ClientSession() as s:
            async with s.get("https://api.themoviedb.org/3/search/multi", params=params, timeout=10) as resp:
                if resp.status != 200: return None, None
                data = await resp.json()
                if data.get('results'):
                    # Find the most relevant result (often the first one with a poster)
                    for res in data.get('results', []):
                        if res.get("poster_path"):
                            poster_url = f"https://image.tmdb.org/t/p/w500{res['poster_path']}"
                            tmdb_id = f"{res.get('media_type', 'none')}-{res.get('id', 'none')}"
                            return poster_url, f"tmdb-{tmdb_id}"
    except Exception: pass
    return None, None

async def _get_poster_and_id(base_name: str, year: str = None):
    """The core waterfall search engine, now returns both URL and a stable ID."""
    logger.info(f"Poster Search: Starting for base_name='{base_name}', year='{year}'")
    
    # Using a simple waterfall search for reliability
    if year:
        url, pid = await _find_poster_from_imdb(f"{base_name} {year}")
        if url: return url, pid
    url, pid = await _find_poster_from_imdb(base_name)
    if url: return url, pid
    if year:
        url, pid = await _find_poster_from_tmdb(base_name, year)
        if url: return url, pid
    url, pid = await _find_poster_from_tmdb(base_name)
    if url: return url, pid
    
    logger.error(f"Poster Search: All attempts failed for base name '{base_name}'.")
    return None, None

async def get_poster_id(base_name: str, year: str = None):
    """Public function that returns only the UNIQUE ID of the found poster for batching."""
    _, poster_id = await _get_poster_and_id(base_name, year)
    return poster_id

async def get_poster(base_name: str, year: str = None):
    """Public function that returns only the POSTER URL for posting."""
    poster_url, _ = await _get_poster_and_id(base_name, year)
    return poster_url
