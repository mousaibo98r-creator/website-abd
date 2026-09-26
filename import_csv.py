import os
import csv
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()
MONGO_URI = os.getenv("MONGO_URI")
client = MongoClient(MONGO_URI)
db = client.miky_db
matrix_col = db.matrix_collection

csv_file = r'f:\web_map-main\mousa_rows (6).csv'

docs = []
try:
    with open(csv_file, 'r', encoding='utf-8', errors='ignore') as f:
        reader = csv.DictReader(f)
        for row in reader:
            country = row.get("destination_country", "").strip().upper()
            if country != 'ALMANYA':
                continue
                
            name = row.get("buyer_name", "").strip()
            if not name:
                continue
                
            email = row.get("email", "")
            ai_status = "scraped" if email.strip() else "pending"
            
            doc = {
                "name": name,
                "location_string": row.get("destination_country", "ALMANYA").strip(),
                "email": email,
                "phone": row.get("phone", ""),
                "website": row.get("website", ""),
                "address": row.get("address", ""),
                "facebook": row.get("facebook", ""),
                "twitter": row.get("twitter", ""),
                "linkedin": row.get("linkedin", ""),
                "instagram": row.get("instagram", ""),
                "youtube": row.get("youtube", ""),
                "ai_status": ai_status,
                "is_matrix": True
            }
            docs.append(doc)
            
    if docs:
        matrix_col.insert_many(docs)
        print(f"Inserted {len(docs)} ALMANYA records from CSV into matrix_collection.")
    else:
        print("No matching ALMANYA records found in CSV to insert.")
except Exception as e:
    print("Error during import:", e)
