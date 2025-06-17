import aiohttp
from bs4 import BeautifulSoup
import logging
import re

logger = logging.getLogger(__name__)

async def get_poster(query: str, year: str = None):
    """
    Finds a poster by scraping IMDb with an improved multi-pass search.
    """
    try:
        # --- Pass 1: Highly specific search with title and year ---
        search_query_with_year = f"{query} {year}".strip() if year else query
        poster_url = await fetch_imdb_poster(search_query_with_year)
        
        if poster_url:
            return poster_url

        # --- Pass 2: Broader search without the year (if first pass failed) ---
        if year is not None:
            logger.warning(f"Poster search failed for '{search_query_with_year}'. Retrying without the year.")
            poster_url = await fetch_imdb_poster(query)
            if poster_url:
                return poster_url
        
        logger.warning(f"All poster search passes failed for query '{query}'.")
        return None

    except Exception as e:
        logger.error(f"An unexpected error occurred during poster scraping for query '{query}': {e}")
        return None

async def fetch_imdb_poster(search_query):
    """The core function to fetch a poster from IMDb for a given query."""
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
                
                # Use a very specific selector to target only the main poster
                img_tag = movie_soup.select_one('div[data-testid="hero-media__poster"] img.ipc-image')
                
                if img_tag and img_tag.get('src'):
                    poster_url = img_tag['src']
                    if '_V1_' in poster_url:
                        poster_url = poster_url.split('_V1_')[0] + "_V1_FMjpg_UX1000_.jpg"
                    
                    # Final verification that the URL is a real image
                    async with session.head(poster_url, timeout=5) as head_resp:
                        if head_resp.status == 200 and 'image' in head_resp.headers.get('Content-Type', ''):
                            logger.info(f"Successfully found and verified poster for '{search_query}'")
                            return poster_url
    except Exception:
        # Don't log full exception for fetch, as it's part of a fallback strategy
        logger.warning(f"A sub-search for poster '{search_query}' failed.")
    return None
