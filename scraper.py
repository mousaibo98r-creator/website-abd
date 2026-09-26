import time
import requests
from duckduckgo_search import DDGS

def search_new_companies(location_query):
    query = f"companies in {location_query}"
    try:
        results = list(DDGS().text(query, max_results=10))
    except Exception as e:
        print(f"DDGS error: {e}")
        return []
        
    companies = []
    session = requests.Session()
    headers = {'User-Agent': 'CompanyApp/1.0 (contact@example.com)'}
    
    for r in results:
        title = r.get('title', '')
        name = title.split('|')[0].split('-')[0].strip()
        
        if not name:
            continue
            
        time.sleep(1) 
        lat, lng = None, None
        
        try:
            geocode_url = "https://nominatim.openstreetmap.org/search"
            params = {
                'q': f"{name} {location_query}",
                'format': 'json',
                'limit': 1
            }
            geo_resp = session.get(geocode_url, params=params, headers=headers).json()
            if geo_resp:
                lat = float(geo_resp[0]['lat'])
                lng = float(geo_resp[0]['lon'])
        except Exception as e:
            print(f"Geocode error for {name}: {e}")
            
        if lat and lng:
            companies.append({
                'name': name,
                'location_string': location_query,
                'latitude': lat,
                'longitude': lng
            })
            
    return companies