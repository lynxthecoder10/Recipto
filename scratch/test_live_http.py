import urllib.request
import urllib.parse

class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

opener = urllib.request.build_opener(NoRedirectHandler)

print("--- Testing Live Flask Server (http://127.0.0.1:5001) ---")

# 1. First reset database using seed script
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from seed_demo_data import main as seed_main
seed_main()

# 2. Test Generate Bill (POST /receipts/download)
data = urllib.parse.urlencode({
    'gala_id': '1',
    'start_cycle': 'C1',
    'end_cycle': 'C1',
    'year': '2026'
}).encode('utf-8')

req = urllib.request.Request('http://127.0.0.1:5001/receipts/download', data=data)
try:
    with opener.open(req) as res:
        content_type = res.headers.get('Content-Type')
        content_disp = res.headers.get('Content-Disposition')
        body = res.read()
        print(f"Generate Bill Response: Status={res.status}, Type={content_type}, Disp={content_disp}, Bytes={len(body)}")
        if body.startswith(b'%PDF'):
            print("  SUCCESS: Valid PDF received!")
        else:
            print("  FAIL: Non-PDF received")
except urllib.error.HTTPError as e:
    print(f"Generate Bill HTTP Error {e.code}: {e.read().decode('utf-8', errors='ignore')}")

# 3. Test Duplicate Bill Generation (Duplicate Prevention)
try:
    with opener.open(req) as res:
        print(f"Duplicate Request Response: Status={res.status}")
except urllib.error.HTTPError as e:
    print(f"Duplicate Request Response: Status={e.code}")

# 4. Test Search Duplicate Bill (GET /receipts/history?gala_number=G001&cycle=C1&year=2026)
req_search = urllib.request.Request('http://127.0.0.1:5001/receipts/history?gala_number=G001&cycle=C1&year=2026')
try:
    with opener.open(req_search) as res:
        body = res.read().decode('utf-8')
        print(f"Search Bill Response: Status={res.status}, Bill Found in HTML: {'BILL/2026/C1/0001' in body}")
except urllib.error.HTTPError as e:
    print(f"Search Bill HTTP Error {e.code}: {e.read().decode('utf-8', errors='ignore')}")

# 5. Test Download Existing Bill (POST /billing/download_existing/BILL/2026/C1/0001)
req_exist = urllib.request.Request('http://127.0.0.1:5001/billing/download_existing/BILL/2026/C1/0001', data=b'')
try:
    with opener.open(req_exist) as res:
        content_type = res.headers.get('Content-Type')
        content_disp = res.headers.get('Content-Disposition')
        body = res.read()
        print(f"Download Existing Bill Response: Status={res.status}, Type={content_type}, Disp={content_disp}, Bytes={len(body)}")
        if body.startswith(b'%PDF'):
            print("  SUCCESS: Valid PDF received!")
        else:
            print("  FAIL: Non-PDF received")
except urllib.error.HTTPError as e:
    print(f"Download Existing Bill HTTP Error {e.code}: {e.read().decode('utf-8', errors='ignore')}")
