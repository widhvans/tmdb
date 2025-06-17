import asyncio
import aiohttp
from bs4 import BeautifulSoup
import logging
import re
from config import Config

logger = logging.getLogger(__name__)


def get_title_similarity(query_title, result_title):
    """A simple similarity score based on word overlap."""
    query_words = set(query_title.lower().split())
    result_words = set(result_title.lower().split())
    if not query_words or not result_words:
        return 0
    intersection = query_words.intersection(result_words)
    return len(intersection) / len(query_words)


async def _fetch_tmdb_candidates(query: str, year: str = None):
    """Internal function to fetch candidates from The Movie Database (TMDB)."""
    candidates = []
    if not Config.TMDB_API_KEY:
        return candidates

    try:
        search_url = "https://api.themoviedb.org/3/search/multi"
        # FIX: include_adult must be a string 'true' or 'false' for aiohttp params
        params = {"api_key": Config.TMDB_API_KEY, "query": query, "include_adult": "false"}
        if year:
            params['year'] = year

        async with aiohttp.ClientSession() as session:
            async with session.get(search_url, params=params, timeout=10) as resp:
                if resp.status != 200:
                    return candidates
                data = await resp.json()
                for result in data.get('results', []):
                    if not result.get("poster_path"): continue
                    
                    media_type = result.get("media_type")
                    title, r_year = None, None
                    if media_type == 'movie':
                        title = result.get('title') or result.get('original_title')
                        if result.get('release_date'): r_year = result.get('release_date').split('-')[0]
                    elif media_type == 'tv':
                        title = result.get('name') or result.get('original_name')
                        if result.get('first_air_date'): r_year = result.get('first_air_date').split('-')[0]
                    
                    if title:
                        candidates.append({'source': 'tmdb', 'title': title.lower(), 'year': r_year, 'url': f"https://image.tmdb.org/t/p/w500{result.get('poster_path')}"})
        return candidates[:5]
    except Exception as e:
        logger.error(f"Error fetching TMDB candidates for query '{query}': {e}")
        return candidates


async def _fetch_imdb_candidate(search_query):
    """Internal function to fetch a single, best-guess candidate from IMDb."""
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
                title = movie_soup.select_one('h1[data-testid="hero__pageTitle"] .hero__primary-text')
                year = movie_soup.select_one('a[href*="/releaseinfo"]')
                img = movie_soup.select_one('div[data-testid="hero-media__poster"] img.ipc-image')
                
                if img and img.get('src') and title:
                    return {'source': 'imdb', 'title': title.text.strip().lower(), 'year': year.text.strip() if year and year.text.strip().isdigit() else None, 'url': img['src'].split('_V1_')[0] + "_V1_FMjpg_UX1000_.jpg"}
    except Exception as e:
        logger.error(f"Error fetching IMDb candidate for query '{search_query}': {e}")
        return None


async def _find_best_poster(query: str, year: str = None):
    """The core logic to search and score posters."""
    logger.info(f"Running poster search for query='{query}', year='{year}'")
    imdb_search_query = f"{query} {year}".strip() if year else query
    tmdb_candidates, imdb_candidate = await asyncio.gather(
        _fetch_tmdb_candidates(query, year),
        _fetch_imdb_candidate(imdb_search_query)
    )

    all_candidates = tmdb_candidates
    if imdb_candidate: all_candidates.append(imdb_candidate)
    if not all_candidates: return None

    best_candidate, highest_score = None, -1
    for candidate in all_candidates:
        score = 0
        if year and candidate.get('year') == year: score += 10
        score += 8 * get_title_similarity(query, candidate['title'])
        if candidate['source'] == 'tmdb': score += 1

        logger.info(f"Candidate: {candidate['title']} ({candidate.get('year')}) | Source: {candidate['source']} | Score: {score:.2f}")
        if score > highest_score:
            highest_score = score
            best_candidate = candidate
            
    if best_candidate and highest_score > 7:  # Lowered confidence threshold
        logger.info(f"Selected best poster: {best_candidate['title']} ({best_candidate['year']}) with score {highest_score:.2f}")
        try:
            async with aiohttp.ClientSession().head(best_candidate['url'], timeout=5) as head_resp:
                if head_resp.status == 200 and 'image' in head_resp.headers.get('Content-Type', ''):
                    return best_candidate['url']
        except Exception:
            logger.warning(f"Winning candidate URL failed verification: {best_candidate['url']}")
    return None


async def get_poster(query: str, year: str = None):
    """
    Two-phase aggressive search for the most accurate poster.
    First tries a precise search, then an aggressive broad search.
    """
    # --- Phase 1: Precise search with original query and year ---
    poster_url = await _find_best_poster(query, year)
    if poster_url:
        return poster_url

    # --- Phase 2: Aggressive fallback search without the year ---
    logger.warning(f"Precise search failed for '{query}'. Starting aggressive fallback search.")
    poster_url = await _find_best_poster(query) # Search again with year=None
    if poster_url:
        return poster_url
    
    logger.error(f"All poster search phases failed for '{query}'.")
    return None
