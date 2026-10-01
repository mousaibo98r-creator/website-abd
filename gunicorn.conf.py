# Gunicorn production configuration
import os

bind = f"0.0.0.0:{os.getenv('PORT', '5000')}"
workers = 1
threads = 4
worker_class = "gthread"
timeout = 180
keepalive = 5
max_requests = 1000
max_requests_jitter = 50
