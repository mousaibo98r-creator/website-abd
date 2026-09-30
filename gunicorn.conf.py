# Gunicorn production configuration
import os

bind = f"0.0.0.0:{os.getenv('PORT', '5000')}"
workers = 2
timeout = 300
keepalive = 5
max_requests = 1000
max_requests_jitter = 50
