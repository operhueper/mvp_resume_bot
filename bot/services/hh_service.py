import logging
import httpx

logger = logging.getLogger(__name__)

async def fetch_hh_vacancies(text_query: str, per_page: int = 20) -> list[dict]:
    """Retrieve exactly `per_page` real vacancies from the HeadHunter API.
    Does not require authorization!
    """
    url = "https://api.hh.ru/vacancies"
    # Basic params: search only in name, text, area=113 (Russia) for market averages
    params = {
        "text": text_query,
        "search_field": "name", 
        "area": "113",  # Russia
        "per_page": per_page,
        "status": "active"
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data.get("items", [])
    except Exception as e:
        logger.error(f"Failed to fetch vacancies from HH API: {e}")
        return []

async def analyze_market_salary(query: str) -> dict:
    """Analyze real salary data from active HH vacancies based on a query."""
    vacancies = await fetch_hh_vacancies(query, per_page=30)
    
    if not vacancies:
        return {"min": 0, "max": 0, "currency": "RUR", "text": "Нет точных рыночных данных на данный момент."}
        
    sals_from = []
    sals_to = []
    
    for v in vacancies:
        salary = v.get("salary")
        if not salary or salary.get("currency") != "RUR":
            continue
        
        if salary.get("from"):
            sals_from.append(salary.get("from"))
        if salary.get("to"):
            sals_to.append(salary.get("to"))
            
    if not sals_from and not sals_to:
        return {"min": 0, "max": 0, "currency": "RUR", "text": "Большинство работодателей не указывает зарплату."}
        
    avg_from = int(sum(sals_from) / len(sals_from)) if sals_from else 0
    avg_to = int(sum(sals_to) / len(sals_to)) if sals_to else 0
    
    if avg_from and avg_to:
        text = f"Рыночная вилка (по {len(sals_from)+len(sals_to)} открытым вакансиям): от {avg_from:,} до {avg_to:,} руб.".replace(',', ' ')
    elif avg_from:
        text = f"Рыночная зарплата (от): {avg_from:,} руб.".replace(',', ' ')
    elif avg_to:
        text = f"Рыночная зарплата (до): {avg_to:,} руб.".replace(',', ' ')
    else:
        text = "Нет точных данных."
        
    return {
        "min": avg_from,
        "max": avg_to,
        "currency": "RUR",
        "text": text,
        "titles": [v.get("name") for v in vacancies[:5]]  # Top 5 real active titles
    }
