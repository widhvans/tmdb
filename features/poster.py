import asyncio
import aiohttp
from bs4 import BeautifulSoup
import logging
import re
from config import Config

logger = logging.getLogger(__name__)

async def _find_poster_from_imdb(query: str):
    """Internal function to get the best-guess poster from IMDb for a single query."""
    try:
        search_url = f"https://www.imdb.com/find?q={re.sub(r'\s+', '+', query)}"
        headers = {'User-Agent': 'Mozilla/5.0', 'Accept-Language': 'en-US,en;q=0.5'}
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(search_url, timeout=10) as resp:
                if resp.status != 200: return None
                soup = BeautifulSoup(await resp.text(), 'html.parser')
                result_link = soup.select_one("a.ipc-metadata-list-summary-item__t")
                if not result_link or not result_link.get('href'): return None
                
                movie_url = "https://www.imdb.com" + result_link['href'].split('?')[0]
                async with session.get(movie_url, timeout=10) as movie_resp:
                    if movie_resp.status != 200: return None
                    movie_soup = BeautifulSoup(await movie_resp.text(), 'html.parser')
                    img_tag = movie_soup.select_one('div[data-testid="hero-media__poster"] img.ipc-image')
                    if img_tag and img_tag.get('src'):
                        poster_url = img_tag['src'].split('_V1_')[0] + "_V1_FMjpg_UX1000_.jpg"
                        return poster_url
    except Exception:
        return None
    return None

async def _find_poster_from_tmdb(query: str, year: str = None):
    """Internal function to get the best-guess poster from TMDB for a single query."""
    if not Config.TMDB_API_KEY: return None
    try:
        search_url = "https://api.themoviedb.org/3/search/multi"
        params = {"api_key": Config.TMDB_API_KEY, "query": query, "include_adult": "false"}
        if year: params['year'] = year
        async with aiohttp.ClientSession() as session:
            async with session.get(search_url, params=params, timeout=10) as resp:
                if resp.status != 200: return None
                data = await resp.json()
                if data.get('results') and data['results'][0].get("poster_path"):
                    return f"https://image.tmdb.org/t/p/w500{data['results'][0]['poster_path']}"
    except Exception:
        return None
    return None

async def get_poster(base_name: str, year: str = None):
    """
    The definitive 'waterfall' poster finder. It uses the clean base_name for searching.
    """
    logger.info(f"Poster Search: Starting for base_name='{base_name}', year='{year}'")

    # Attempt 1: IMDb with Year
    if year:
        poster = await _find_poster_from_imdb(f"{base_name} {year}")
        if poster: logger.info(f"SUCCESS: IMDb with year for '{base_name}'"); return poster
    
    # Attempt 2: IMDb without Year
    poster = await _find_poster_from_imdb(base_name)
    if poster: logger.info(f"SUCCESS: IMDb without year for '{base_name}'"); return poster
    
    # Attempt 3: TMDB with Year
    if year:
        poster = await _find_poster_from_tmdb(base_name, year)
        if poster: logger.info(f"SUCCESS: TMDB with year for '{base_name}'"); return poster
    
    # Attempt 4: TMDB without Year
    poster = await _find_poster_from_tmdb(base_name)
    if poster: logger.info(f"SUCCESS: TMDB without year for '{base_name}'"); return poster

    logger.error(f"Poster Search: All attempts failed for base name '{base_name}'.")
    return None
