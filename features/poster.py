
import asyncio
import aiohttp
from bs4 import BeautifulSoup
import logging
import re
from config import Config

logger = logging.getLogger(__name__)

def generate_search_queries(title: str):
    """
    Generates a list of progressively shorter search queries from a title,
    based on your smart suggestion.
    Example: 'The Lord of the Rings The Fellowship' -> 
             ['The Lord of the Rings The Fellowship', 'The Lord of the Rings', 'The Lord of']
    """
    words = title.split()
    queries = []
    # Generate queries from the full title down to 2 words (or less if the title is short)
    for i in range(len(words), max(0, min(2, len(words)) - 1), -1):
        if i > 0:
            queries.append(' '.join(words[:i]))
    # Return unique queries, removing duplicates
    return list(dict.fromkeys(queries))

async def _fetch_imdb_simple(search_query: str):
    """
    This is the simple and effective IMDb scraping code you provided.
    It's now the primary search method.
    """
    try:
        search_query_encoded = re.sub(r'\s+', '+', search_query)
        search_url = f"https://www.imdb.com/find?q={search_query_encoded}"
        headers = {'User-Agent': 'Mozilla/5.0', 'Accept-Language': 'en-US,en;q=0.5'}
        
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(search_url, timeout=10) as resp:
                if resp.status != 200: return None
                soup = BeautifulSoup(await resp.text(), 'html.parser')
                result_link_tag = soup.select_one("a.ipc-metadata-list-summary-item__t")
                if not result_link_tag or not result_link_tag.get('href'): return None
                movie_url = "https://www.imdb.com" + result_link_tag['href'].split('?')[0]

            async with session.get(movie_url, timeout=10) as movie_resp:
                if movie_resp.status != 200: return None
                movie_soup = BeautifulSoup(await movie_resp.text(), 'html.parser')
                img_tag = movie_soup.select_one('div[data-testid="hero-media__poster"] img.ipc-image')
                
                if img_tag and img_tag.get('src'):
                    poster_url = img_tag['src'].split('_V1_')[0] + "_V1_FMjpg_UX1000_.jpg"
                    # Final verification that the URL is a real image
                    async with session.head(poster_url, timeout=5) as head_resp:
                        if head_resp.status == 200 and 'image' in head_resp.headers.get('Content-Type', ''):
                            logger.info(f"SUCCESS with IMDb Simple for '{search_query}'")
                            return poster_url
    except Exception:
        logger.warning(f"IMDb Simple search failed for '{search_query}'.")
    return None

async def _fetch_tmdb_poster(query: str, year: str = None):
    """A simplified TMDB fetcher used as a secondary method."""
    if not Config.TMDB_API_KEY: return None
    try:
        search_url = "https://api.themoviedb.org/3/search/multi"
        params = {"api_key": Config.TMDB_API_KEY, "query": query, "include_adult": "false"}
        if year:
            params['year'] = year

        async with aiohttp.ClientSession() as session:
            async with session.get(search_url, params=params, timeout=10) as resp:
                if resp.status != 200: return None
                data = await resp.json()
                # Return the first valid poster found
                for result in data.get('results', []):
                    if result.get("poster_path"):
                        poster_url = f"https://image.tmdb.org/t/p/w500{result.get('poster_path')}"
                        logger.info(f"SUCCESS with TMDB for '{query}'")
                        return poster_url
    except Exception:
        logger.warning(f"TMDB search failed for '{query}'.")
    return None

async def get_poster(query: str, year: str = None):
    """
    The ultimate aggressive poster finder.
    It generates truncated queries and tries both IMDb and TMDB for each one.
    """
    # 1. Generate the list of search queries from most specific to most broad
    search_queries = generate_search_queries(query)
    logger.info(f"Starting ultimate poster search for base query '{query}'. Generated queries: {search_queries}")

    # 2. Loop through each generated query and try all methods
    for i, search_query in enumerate(search_queries):
        logger.info(f"--- Attempting search with query #{i+1}: '{search_query}' ---")
        
        # Attempt 1: Your IMDb code with Year
        if year:
            poster = await _fetch_imdb_simple(f"{search_query} {year}")
            if poster: return poster

        # Attempt 2: TMDB with Year
        if year:
            poster = await _fetch_tmdb_poster(search_query, year)
            if poster: return poster

        # Attempt 3: Your IMDb code without Year (most reliable)
        poster = await _fetch_imdb_simple(search_query)
        if poster: return poster

        # Attempt 4: TMDB without Year
        poster = await _fetch_tmdb_poster(search_query)
        if poster: return poster

    # 3. If all attempts for all queries fail
    logger.error(f"All poster search attempts failed for base query '{query}'.")
    return None
