web: PLAYWRIGHT_BROWSERS_PATH=/app/pw-browsers playwright install --with-deps chromium 2>/dev/null; gunicorn -b 0.0.0.0:$PORT -w 1 --timeout 120 --preload quiz_api_server:app
