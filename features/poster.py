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
    # Give more weight to the query being a subset of the result
    return len(intersection) / len(query_words)


async def fetch_tmdb_candidates(query: str, year: str = None):
    """
    Fetches a list of poster candidates from The Movie Database (TMDB).
    Each candidate is a dict with title, year, and poster_url.
    """
    candidates = []
    if not Config.TMDB_API_KEY:
        logger.warning("TMDB API key not configured. Skipping TMDB search.")
        return candidates

    try:
        search_url = "https://api.themoviedb.org/3/search/multi"
        params = {"api_key": Config.TMDB_API_KEY, "query": query, "include_adult": False}
        if year:
            params['year'] = year

        async with aiohttp.ClientSession() as session:
            async with session.get(search_url, params=params, timeout=10) as resp:
                if resp.status != 200:
                    return candidates
                
                data = await resp.json()
                results = data.get('results', [])

                for result in results:
                    poster_path = result.get("poster_path")
                    if not poster_path:
                        continue
                    
                    media_type = result.get("media_type")
                    result_title, result_year = None, None

                    if media_type == 'movie':
                        result_title = result.get('title') or result.get('original_title')
                        if result.get('release_date'):
                            result_year = result.get('release_date').split('-')[0]
                    elif media_type == 'tv':
                        result_title = result.get('name') or result.get('original_name')
                        if result.get('first_air_date'):
                            result_year = result.get('first_air_date').split('-')[0]
                    
                    if result_title:
                        candidates.append({
                            'source': 'tmdb',
                            'title': result_title.lower(),
                            'year': result_year,
                            'url': f"https://image.tmdb.org/t/p/w500{poster_path}"
                        })
        # Return top 5 candidates to analyze
        return candidates[:5]
    except Exception as e:
        logger.error(f"Error fetching TMDB candidates: {e}")
        return candidates


async def fetch_imdb_candidate(search_query):
    """Fetches a single, best-guess candidate from IMDb."""
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

                title_tag = movie_soup.select_one('h1[data-testid="hero__pageTitle"] .hero__primary-text')
                title = title_tag.text.strip().lower() if title_tag else None

                year_tag = movie_soup.select_one('a[href*="/releaseinfo"]')
                year = year_tag.text.strip() if year_tag and year_tag.text.strip().isdigit() else None
                
                img_tag = movie_soup.select_one('div[data-testid="hero-media__poster"] img.ipc-image')
                
                if img_tag and img_tag.get('src') and title:
                    poster_url = img_tag['src'].split('_V1_')[0] + "_V1_FMjpg_UX1000_.jpg"
                    return { 'source': 'imdb', 'title': title, 'year': year, 'url': poster_url }
    except Exception as e:
        logger.error(f"Error fetching IMDb candidate: {e}")
        return None


async def get_poster(query: str, year: str = None):
    """
    Intelligently finds the best poster by fetching candidates from TMDB and IMDb,
    scoring them, and selecting the best match.
    """
    logger.info(f"Starting smart poster search for query='{query}', year='{year}'")
    
    # --- Step 1: Fetch candidates concurrently ---
    imdb_search_query = f"{query} {year}".strip() if year else query
    tmdb_candidates, imdb_candidate = await asyncio.gather(
        fetch_tmdb_candidates(query, year),
        fetch_imdb_candidate(imdb_search_query)
    )

    all_candidates = tmdb_candidates
    if imdb_candidate:
        all_candidates.append(imdb_candidate)

    if not all_candidates:
        logger.warning(f"No poster candidates found for '{query}' from any source.")
        return None

    # --- Step 2: Score and select the best candidate ---
    best_candidate = None
    highest_score = -1

    for candidate in all_candidates:
        score = 0
        
        # Score based on Year match (very important)
        if year and candidate.get('year') == year:
            score += 10
        
        # Score based on Title similarity
        similarity = get_title_similarity(query, candidate['title'])
        score += 8 * similarity
        
        # Prefer TMDB for same-level matches
        if candidate['source'] == 'tmdb':
            score += 1

        logger.info(f"Candidate: {candidate['title']} ({candidate.get('year')}) | Source: {candidate['source']} | Score: {score:.2f}")

        if score > highest_score:
            highest_score = score
            best_candidate = candidate
            
    # --- Step 3: Final decision with confidence threshold ---
    if best_candidate and highest_score > 8:  # Confidence threshold
        logger.info(f"Selected best poster: {best_candidate['title']} ({best_candidate['year']}) with score {highest_score:.2f}")
        
        # Final verification that the image URL is valid
        try:
            async with aiohttp.ClientSession() as session:
                 async with session.head(best_candidate['url'], timeout=5) as head_resp:
                    if head_resp.status == 200 and 'image' in head_resp.headers.get('Content-Type', ''):
                        return best_candidate['url']
        except Exception as e:
            logger.warning(f"Winning candidate URL failed verification: {best_candidate['url']}. Error: {e}")

    logger.warning(f"Could not find a confident poster match for '{query}'. All candidates scored too low.")
    return None
