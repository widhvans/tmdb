import asyncio
import aiohttp
from bs4 import BeautifulSoup
import logging
import re
from config import Config

logger = logging.getLogger(__name__)

def generate_search_queries(title: str):
    """Generates a list of progressively shorter search queries from a title."""
    words = title.split()
    queries = []
    for i in range(len(words), max(0, min(2, len(words)) - 1), -1):
        if i > 0:
            queries.append(' '.join(words[:i]))
    return list(dict.fromkeys(queries))

def get_title_similarity(query_title, result_title):
    """A simple similarity score based on word overlap."""
    query_words = set(query_title.lower().split())
    result_words = set(result_title.lower().split())
    if not query_words or not result_words: return 0
    return len(query_words.intersection(result_words)) / len(query_words)

async def _fetch_imdb_candidates(query: str, priority: int):
    """Fetches candidates from IMDb and attaches a priority score."""
    candidates = []
    try:
        search_url = f"https://www.imdb.com/find?q={re.sub(r'\s+', '+', query)}"
        headers = {'User-Agent': 'Mozilla/5.0', 'Accept-Language': 'en-US,en;q=0.5'}
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(search_url, timeout=10) as resp:
                if resp.status != 200: return candidates
                soup = BeautifulSoup(await resp.text(), 'html.parser')
                # Find top 3 results
                for result_link_tag in soup.select("a.ipc-metadata-list-summary-item__t")[:3]:
                    movie_url = "https://www.imdb.com" + result_link_tag['href'].split('?')[0]
                    async with session.get(movie_url, timeout=10) as movie_resp:
                        if movie_resp.status != 200: continue
                        movie_soup = BeautifulSoup(await movie_resp.text(), 'html.parser')
                        title = movie_soup.select_one('h1 .hero__primary-text')
                        year = movie_soup.select_one('a[href*="/releaseinfo"]')
                        img = movie_soup.select_one('div[data-testid="hero-media__poster"] img.ipc-image')
                        if img and img.get('src') and title:
                            candidates.append({
                                'source': 'imdb', 'priority': priority,
                                'title': title.text.strip().lower(),
                                'year': year.text.strip() if year and year.text.strip().isdigit() else None,
                                'url': img['src'].split('_V1_')[0] + "_V1_FMjpg_UX1000_.jpg"
                            })
        return candidates
    except Exception:
        return candidates

async def _fetch_tmdb_candidates(query: str, year: str, priority: int):
    """Fetches candidates from TMDB and attaches a priority score."""
    candidates = []
    if not Config.TMDB_API_KEY: return candidates
    try:
        search_url = "https://api.themoviedb.org/3/search/multi"
        params = {"api_key": Config.TMDB_API_KEY, "query": query, "include_adult": "false"}
        if year: params['year'] = year
        async with aiohttp.ClientSession() as session:
            async with session.get(search_url, params=params, timeout=10) as resp:
                if resp.status != 200: return candidates
                data = await resp.json()
                for result in data.get('results', []):
                    if not result.get("poster_path"): continue
                    title, r_year = None, None
                    if result.get("media_type") == 'movie':
                        title = result.get('title')
                        if result.get('release_date'): r_year = result.get('release_date').split('-')[0]
                    elif result.get("media_type") == 'tv':
                        title = result.get('name')
                        if result.get('first_air_date'): r_year = result.get('first_air_date').split('-')[0]
                    if title:
                        candidates.append({
                            'source': 'tmdb', 'priority': priority, 'title': title.lower(), 'year': r_year,
                            'url': f"https://image.tmdb.org/t/p/w500{result.get('poster_path')}"
                        })
        return candidates
    except Exception:
        return candidates

async def get_poster(query: str, year: str = None):
    """The new evidence-based poster engine."""
    logger.info(f"Starting evidence-based poster search for query='{query}', year='{year}'")
    search_queries = generate_search_queries(query)
    
    tasks = []
    for i, sq in enumerate(search_queries):
        priority = len(search_queries) - i  # Longer queries get higher priority
        tasks.append(_fetch_imdb_candidates(sq, priority))
        tasks.append(_fetch_tmdb_candidates(sq, year, priority))
        if year: # Add a high-priority task for exact year matches
             tasks.append(_fetch_imdb_candidates(f"{sq} {year}", priority + 5))

    results = await asyncio.gather(*tasks)
    
    all_candidates = [candidate for res_list in results if res_list for candidate in res_list]
    if not all_candidates:
        logger.error(f"Poster search failed. No candidates found for '{query}'.")
        return None

    best_candidate, highest_score = None, -1
    for cand in all_candidates:
        score = cand['priority'] * 2  # Base score from query specificity
        score += get_title_similarity(query, cand['title']) * 10
        if year and cand['year'] == year: score += 15
        if cand['source'] == 'tmdb': score += 2
        
        if score > highest_score:
            highest_score = score
            best_candidate = cand

    if best_candidate and highest_score > 15: # Confidence threshold
        logger.info(f"Selected best poster: {best_candidate['title']} ({best_candidate.get('year')}) with score {highest_score:.2f}")
        return best_candidate['url']
    
    logger.error(f"Could not find a confident poster match for '{query}'. Highest score: {highest_score:.2f}")
    return None
