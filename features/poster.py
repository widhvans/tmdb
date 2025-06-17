import aiohttp
from bs4 import BeautifulSoup
import logging
import re
from config import Config

logger = logging.getLogger(__name__)

async def fetch_tmdb_poster(query: str, year: str = None):
    """
    Fetches a poster from The Movie Database (TMDB).
    """
    if not Config.TMDB_API_KEY:
        logger.warning("TMDB_API_KEY not found in config. Skipping TMDB search.")
        return None

    try:
        search_url = "https://api.themoviedb.org/3/search/multi"
        params = {
            "api_key": Config.TMDB_API_KEY,
            "query": query,
            "include_adult": False
        }
        if year:
            params['year'] = year

        async with aiohttp.ClientSession() as session:
            async with session.get(search_url, params=params) as resp:
                if resp.status != 200:
                    logger.error(f"TMDB API request failed with status {resp.status} for query '{query}'")
                    return None
                
                data = await resp.json()
                results = data.get('results', [])

                if not results:
                    return None

                # The first result is often the most relevant.
                best_result = results[0]
                
                poster_path = best_result.get("poster_path")
                if poster_path:
                    # Using w500 provides a good balance of quality and size.
                    poster_url = f"https://image.tmdb.org/t/p/w500{poster_path}"
                    logger.info(f"Successfully found TMDB poster for '{query}'")
                    return poster_url
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred during TMDB poster search for query '{query}': {e}")
        return None

async def get_poster(query: str, year: str = None):
    """
    Finds a poster by first trying TMDB and then falling back to IMDb scraping.
    """
    try:
        # --- Pass 1: Try TMDB first (more reliable and better quality) ---
        tmdb_poster = await fetch_tmdb_poster(query, year)
        if tmdb_poster:
            return tmdb_poster

        logger.warning(f"TMDB search failed for '{query}'. Falling back to IMDb scraping.")

        # --- Pass 2: Highly specific IMDb search with title and year ---
        search_query_with_year = f"{query} {year}".strip() if year else query
        imdb_poster_url = await fetch_imdb_poster(search_query_with_year)
        
        if imdb_poster_url:
            return imdb_poster_url

        # --- Pass 3: Broader IMDb search without the year (if first pass failed) ---
        if year is not None:
            logger.warning(f"IMDb poster search failed for '{search_query_with_year}'. Retrying without the year.")
            imdb_poster_url = await fetch_imdb_poster(query)
            if imdb_poster_url:
                return imdb_poster_url
        
        logger.warning(f"All poster search passes (TMDB & IMDb) failed for query '{query}'.")
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred during get_poster for query '{query}': {e}")
        return None

async def fetch_imdb_poster(search_query):
    """The core function to fetch a poster from IMDb for a given query (Fallback)."""
    try:
        search_query_encoded = re.sub(r'\s+', '+', search_query)
        search_url = f"https://www.imdb.com/find?q={search_query_encoded}"
        headers = {'User-Agent': 'Mozilla/5.0', 'Accept-Language': 'en-US,en;q=0.5'}
        
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(search_url) as resp:
                if resp.status != 200: return None
                soup = BeautifulSoup(await resp.text(), 'html.parser')
                result_link_tag = soup.select_one("a.ipc-metadata-list-summary-item__t")
                if not result_link_tag or not result_link_tag.get('href'): return None
                movie_url = "https://www.imdb.com" + result_link_tag['href'].split('?')[0]

            async with session.get(movie_url) as movie_resp:
                if movie_resp.status != 200: return None
                movie_soup = BeautifulSoup(await movie_resp.text(), 'html.parser')
                
                img_tag = movie_soup.select_one('div[data-testid="hero-media__poster"] img.ipc-image')
                
                if img_tag and img_tag.get('src'):
                    poster_url = img_tag['src']
                    if '_V1_' in poster_url:
                        poster_url = poster_url.split('_V1_')[0] + "_V1_FMjpg_UX1000_.jpg"
                    
                    # Final verification that the URL is a real image
                    async with session.head(poster_url, timeout=5) as head_resp:
                        if head_resp.status == 200 and 'image' in head_resp.headers.get('Content-Type', ''):
                            logger.info(f"Successfully found and verified IMDb poster for '{search_query}'")
                            return poster_url
    except Exception:
        logger.warning(f"A sub-search for IMDb poster '{search_query}' failed.")
    return None
