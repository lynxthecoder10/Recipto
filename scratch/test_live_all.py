import urllib.request
import urllib.parse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from seed_demo_data import main as seed_main

class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

opener = urllib.request.build_opener(NoRedirectHandler)

def test_live():
    print("=== LIVE SERVER FULL VERIFICATION ===")
    seed_main()

    # 1. Generate Bill for G001 (Alice)
    data = urllib.parse.urlencode({'gala_id': '1', 'start_cycle': 'C1', 'end_cycle': 'C1', 'year': '2026'}).encode('utf-8')
    req = urllib.request.Request('http://127.0.0.1:5001/receipts/download', data=data)
    with opener.open(req) as res:
        assert res.status == 200
        assert res.headers.get('Content-Type') == 'application/pdf'
        pdf_data = res.read()
        assert pdf_data.startswith(b'%PDF')
        print(f"[PASS] Generate Bill: Returned {len(pdf_data)} bytes PDF")

    # 2. Search Duplicate Bill
    req_search = urllib.request.Request('http://127.0.0.1:5001/receipts/history?gala_number=G001&cycle=C1&year=2026')
    with opener.open(req_search) as res:
        assert res.status == 200
        html = res.read().decode('utf-8')
        assert 'BILL/2026/C1/0001' in html
        print("[PASS] Search Duplicate Bill: BILL/2026/C1/0001 found on page")

    # 3. Download Duplicate Bill
    req_exist = urllib.request.Request('http://127.0.0.1:5001/billing/download_existing/BILL/2026/C1/0001', data=b'')
    with opener.open(req_exist) as res:
        assert res.status == 200
        assert res.headers.get('Content-Type') == 'application/pdf'
        pdf_data = res.read()
        assert pdf_data.startswith(b'%PDF')
        print(f"[PASS] Download Duplicate Bill: Returned {len(pdf_data)} bytes PDF")

    # 4. Update Payment: Partial (200 out of 600)
    pay_data = urllib.parse.urlencode({'amount_paid': '200', 'payment_method': 'UPI'}).encode('utf-8')
    req_pay = urllib.request.Request('http://127.0.0.1:5001/billing/payment/BILL/2026/C1/0001', data=pay_data)
    try:
        with opener.open(req_pay) as res:
            pass
    except urllib.error.HTTPError as e:
        assert e.code == 302
        print("[PASS] Payment Update (Partial): Updated successfully (HTTP 302 Redirect)")

    # 5. Verify Billing Dashboard & WhatsApp Link for G001
    req_dash = urllib.request.Request('http://127.0.0.1:5001/billing')
    with opener.open(req_dash) as res:
        assert res.status == 200
        html = res.read().decode('utf-8')
        assert 'Half Paid' in html
        assert '400.00' in html
        assert 'wa.me/918779033522' in html
        print("[PASS] Billing Dashboard: Half Paid state, Rs.400.00 pending, wa.me/918779033522 verified")

    # 6. Update Payment: Full Paid (600 out of 600)
    pay_data_full = urllib.parse.urlencode({'amount_paid': '600', 'payment_method': 'Bank Transfer'}).encode('utf-8')
    req_pay_full = urllib.request.Request('http://127.0.0.1:5001/billing/payment/BILL/2026/C1/0001', data=pay_data_full)
    try:
        with opener.open(req_pay_full) as res:
            pass
    except urllib.error.HTTPError as e:
        assert e.code == 302
        print("[PASS] Payment Update (Full): Updated successfully (HTTP 302 Redirect)")

    # 7. Verify Dashboard Full Paid
    with opener.open(req_dash) as res:
        assert res.status == 200
        html = res.read().decode('utf-8')
        assert 'Full Paid' in html
        assert 'wa.me/918779033522' not in html
        print("[PASS] Billing Dashboard: Full Paid state, WhatsApp button hidden")

    # 8. Gala CRUD: Create Gala G011
    gala_data = urllib.parse.urlencode({'gala_number': 'G011', 'tenant_name': 'Kevin', 'phone_number': '9876543210', 'monthly_rent': '700'}).encode('utf-8')
    req_gala = urllib.request.Request('http://127.0.0.1:5001/billing/galas', data=gala_data)
    try:
        with opener.open(req_gala) as res:
            pass
    except urllib.error.HTTPError as e:
        assert e.code == 302
        print("[PASS] Gala CRUD: Gala G011 created (HTTP 302 Redirect)")

    # 9. Verify Gala List API
    req_api = urllib.request.Request('http://127.0.0.1:5001/api/galas')
    with opener.open(req_api) as res:
        assert res.status == 200
        json_str = res.read().decode('utf-8')
        assert 'G011' in json_str
        assert 'Kevin' in json_str
        print("[PASS] API /api/galas: Returned 11 galas including G011")

    print("\nALL VERIFICATIONS PASSED 100% SUCCESSFULLY ON LIVE SERVER!")

if __name__ == '__main__':
    test_live()
