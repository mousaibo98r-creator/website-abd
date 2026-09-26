import os
import json
import asyncio
from bson import ObjectId
from flask import Flask, render_template, request, jsonify, Response
from pymongo import MongoClient, DESCENDING, ASCENDING
from dotenv import load_dotenv

from scraper import search_new_companies
from deepseek_client import DeepSeekClient

load_dotenv()

app = Flask(__name__)

# 1. DATABASE SETUP
MONGO_URI = os.getenv("MONGO_URI")
client = MongoClient(MONGO_URI)
db = client.miky_db

search_collection = db.search_collection
matrix_collection = db.matrix_collection

# global_collection removed as per architecture requirements
# global_collection = db.global_collection

# Helper for JSON serialization
def serialize_doc(doc):
    if doc and '_id' in doc:
        doc['_id'] = str(doc['_id'])
    return doc

def build_filter_query(req):
    """Build a MongoDB filter query from request query parameters.
    
    Supports:
      - q: text search on 'name' field ($regex, case-insensitive)
      - city: text search on 'location_string' field ($regex, case-insensitive)
      - status: exact match on 'ai_status' (skip if 'all')
      - has_email: 'true' => email exists and is non-empty, 'false' => missing/empty
      - has_phone: 'true' => phone exists and is non-empty, 'false' => missing/empty
      - has_website: 'true' => website exists and is non-empty
    """
    query = {}
    
    # Text Search (q) — searches both 'name' and 'buyer_name' fields
    q = req.args.get('q', '').strip()
    if q:
        query['$or'] = [
            {'name': {'$regex': q, '$options': 'i'}},
            {'buyer_name': {'$regex': q, '$options': 'i'}}
        ]
        
    # Location/City — searches 'location_string' and 'destination_country'
    city = req.args.get('city', '').strip()
    if city:
        if '$or' in query:
            # Combine with existing $or using $and
            existing_or = query.pop('$or')
            query['$and'] = [
                {'$or': existing_or},
                {'$or': [
                    {'location_string': {'$regex': city, '$options': 'i'}},
                    {'destination_country': {'$regex': city, '$options': 'i'}}
                ]}
            ]
        else:
            query['$or'] = [
                {'location_string': {'$regex': city, '$options': 'i'}},
                {'destination_country': {'$regex': city, '$options': 'i'}}
            ]
        
    # AI Status
    status = req.args.get('status', '').strip()
    if status and status != 'all':
        query['ai_status'] = status
        
    # Has Email — database-level filter
    has_email = req.args.get('has_email', '').strip()
    if has_email == 'true':
        query['email'] = {'$exists': True, '$nin': [None, ""]}
    elif has_email == 'false':
        query['$or'] = query.get('$or', [])
        # Use direct filter — email missing or empty
        query['email'] = {'$in': [None, ""]}
        
    # Has Phone — database-level filter
    has_phone = req.args.get('has_phone', '').strip()
    if has_phone == 'true':
        query['phone'] = {'$exists': True, '$nin': [None, ""]}
    elif has_phone == 'false':
        query['phone'] = {'$in': [None, ""]}

    # Has Website — database-level filter
    has_website = req.args.get('has_website', '').strip()
    if has_website == 'true':
        query['website'] = {'$exists': True, '$nin': [None, ""]}
        
    return query

def get_sort_param(req):
    sort_param = req.args.get('sort', 'newest')
    return [('_id', DESCENDING if sort_param == 'newest' else ASCENDING)]


# --- UI ROUTES ---
@app.route('/')
def index_page():
    return render_template('index.html')

@app.route('/search')
def search_page():
    return render_template('search.html')

@app.route('/matrix')
def matrix_page():
    return render_template('matrix.html')


# --- MONGODB API ROUTES ---

@app.route('/api/get_search_data', methods=['GET'])
def get_search_data():
    query = build_filter_query(request)
    sort_order = get_sort_param(request)
    
    # Pagination support
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 100))
    skip = (page - 1) * per_page
    
    total = search_collection.count_documents(query)
    docs = list(search_collection.find(query).sort(sort_order).skip(skip).limit(per_page))
    
    return jsonify({
        'data': [serialize_doc(doc) for doc in docs],
        'total': total,
        'page': page,
        'per_page': per_page
    })

@app.route('/api/get_matrix_data', methods=['GET'])
def get_matrix_data():
    query = build_filter_query(request)
    sort_order = get_sort_param(request)
    
    # Pagination support
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 100))
    skip = (page - 1) * per_page
    
    total = matrix_collection.count_documents(query)
    docs = list(matrix_collection.find(query).sort(sort_order).skip(skip).limit(per_page))
    
    return jsonify({
        'data': [serialize_doc(doc) for doc in docs],
        'total': total,
        'page': page,
        'per_page': per_page
    })

@app.route('/api/get_matrix_stats', methods=['GET'])
def get_matrix_stats():
    """Returns aggregate statistics for the Matrix Hub dashboard."""
    pipeline = [
        {
            '$facet': {
                'total': [{'$count': 'count'}],
                'with_email': [
                    {'$match': {'email': {'$exists': True, '$nin': [None, ""]}}},
                    {'$count': 'count'}
                ],
                'with_phone': [
                    {'$match': {'phone': {'$exists': True, '$nin': [None, ""]}}},
                    {'$count': 'count'}
                ],
                'with_website': [
                    {'$match': {'website': {'$exists': True, '$nin': [None, ""]}}},
                    {'$count': 'count'}
                ],
                'status_pending': [
                    {'$match': {'ai_status': 'pending'}},
                    {'$count': 'count'}
                ],
                'status_completed': [
                    {'$match': {'ai_status': 'completed'}},
                    {'$count': 'count'}
                ],
                'status_scraped': [
                    {'$match': {'ai_status': 'scraped'}},
                    {'$count': 'count'}
                ]
            }
        }
    ]
    
    result = list(matrix_collection.aggregate(pipeline))
    
    def extract_count(facet_result):
        return facet_result[0]['count'] if facet_result else 0
    
    if result:
        r = result[0]
        stats = {
            'total': extract_count(r.get('total', [])),
            'with_email': extract_count(r.get('with_email', [])),
            'with_phone': extract_count(r.get('with_phone', [])),
            'with_website': extract_count(r.get('with_website', [])),
            'status_pending': extract_count(r.get('status_pending', [])),
            'status_completed': extract_count(r.get('status_completed', [])),
            'status_scraped': extract_count(r.get('status_scraped', []))
        }
    else:
        stats = {
            'total': 0, 'with_email': 0, 'with_phone': 0,
            'with_website': 0, 'status_pending': 0,
            'status_completed': 0, 'status_scraped': 0
        }
    
    return jsonify(stats)

@app.route('/api/get_search_stats', methods=['GET'])
def get_search_stats():
    """Returns aggregate statistics for the Discovery Search dashboard."""
    pipeline = [
        {
            '$facet': {
                'total': [{'$count': 'count'}],
                'with_email': [
                    {'$match': {'email': {'$exists': True, '$nin': [None, ""]}}},
                    {'$count': 'count'}
                ],
                'with_phone': [
                    {'$match': {'phone': {'$exists': True, '$nin': [None, ""]}}},
                    {'$count': 'count'}
                ],
                'with_website': [
                    {'$match': {'website': {'$exists': True, '$nin': [None, ""]}}},
                    {'$count': 'count'}
                ]
            }
        }
    ]
    
    result = list(search_collection.aggregate(pipeline))
    
    def extract_count(facet_result):
        return facet_result[0]['count'] if facet_result else 0
    
    if result:
        r = result[0]
        stats = {
            'total': extract_count(r.get('total', [])),
            'with_email': extract_count(r.get('with_email', [])),
            'with_phone': extract_count(r.get('with_phone', [])),
            'with_website': extract_count(r.get('with_website', []))
        }
    else:
        stats = {'total': 0, 'with_email': 0, 'with_phone': 0, 'with_website': 0}
    
    return jsonify(stats)

@app.route('/api/migrate_to_matrix', methods=['POST'])
def migrate_to_matrix():
    data = request.json
    doc_id = data.get('id')
    if not doc_id:
        return jsonify({"error": "id is required"}), 400
        
    doc = search_collection.find_one({"_id": ObjectId(doc_id)})
    if not doc:
        return jsonify({"error": "Not found in search_collection"}), 404
        
    doc['ai_status'] = 'pending'
    matrix_collection.insert_one(doc)
    search_collection.delete_one({"_id": ObjectId(doc_id)})
    
    return jsonify({"message": "Successfully migrated to matrix"})

# --- EXISTING FUNCTIONALITY ---

@app.route('/api/search_new', methods=['POST'])
def api_search_new():
    data = request.json
    location = data.get('location', '')
    if not location:
        return jsonify({"error": "Location is required"}), 400
        
    companies = search_new_companies(location)
    inserted = 0
    if companies:
        for c in companies:
            if 'company_id' in c:
                del c['company_id']
        try:
            search_collection.insert_many(companies)
            inserted = len(companies)
        except Exception as e:
            print("Mongo Error:", e)
            
    return jsonify({"message": f"Found and saved {inserted} new companies", "count": inserted})

@app.route('/api/enrich')
def api_enrich():
    def generate():
        pending_docs = list(matrix_collection.find({"ai_status": "pending"}))
        yield f"data: {json.dumps({'message': f'Starting enrichment for {len(pending_docs)} companies'})}\n\n"
        
        client_ai = DeepSeekClient()
        
        for comp in pending_docs:
            c_name = comp.get("name", "")
            yield f"data: {json.dumps({'message': f'Enriching {c_name}...'})}\n\n"
            
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                system_prompt = "You are a data researcher finding contact details for companies."
                data_found, turns = loop.run_until_complete(
                    client_ai.extract_company_data(system_prompt, comp.get('name', ''), comp.get('location_string', ''))
                )
                
                email = None
                phone = None
                if data_found:
                    try:
                        parsed = json.loads(data_found) if isinstance(data_found, str) else data_found
                        emails = parsed.get("emails_found", [])
                        phones = parsed.get("phones_found", [])
                        email = emails[0] if emails else None
                        phone = phones[0] if phones else None
                    except:
                        pass
                
                matrix_collection.update_one(
                    {"_id": comp["_id"]},
                    {"$set": {
                        "email": email,
                        "phone": phone,
                        "ai_status": "completed"
                    }}
                )
                
                c_name = comp.get("name", "")
                yield f"data: {json.dumps({'message': f'Finished {c_name}: Email={email}, Phone={phone}'})}\n\n"
            except Exception as e:
                c_name = comp.get("name", "")
                yield f"data: {json.dumps({'message': f'Error for {c_name}: {str(e)}'})}\n\n"
            finally:
                loop.run_until_complete(client_ai.close())
                loop.close()
                
        yield f"data: {json.dumps({'message': 'Enrichment complete', 'done': True})}\n\n"

    return Response(generate(), mimetype='text/event-stream')

if __name__ == '__main__':
    app.run(debug=True)
