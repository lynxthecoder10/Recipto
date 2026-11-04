import re
from html import unescape
import io
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

html_sample = """
    <div class="copy-label">Owner Copy</div>
    <div class="owner-name">M. KANDA KUMARAN</div>
    <td class="label">Bill No.</td>
    <td class="value">BILL/2026/C1/0001</td>
    <td class="right-label">Date</td>
    <td class="value">15/08/2026</td>
    <td class="label">Tenant Name</td>
    <td class="value" colspan="3">Alice</td>
    <td class="label">Rent of Gala/House No.</td>
    <td class="value" colspan="3">G001</td>
    <td class="label">Month</td>
    <td class="value" colspan="3">Jan - Mar 2026</td>
    <p class="sentence">Received from <strong>Alice</strong> a sum of <strong>Rs. 600.00</strong> for the rent of Gala No. <strong>G001</strong> for the month of <strong>Jan - Mar 2026</strong>.</p>
"""

def extract_field(pattern, text, default=''):
    m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    return unescape(m.group(1).strip()) if m else default

bill_no = extract_field(r'Bill No\..*?<td[^>]*class=["\']value["\'][^>]*>(.*?)</td>', html_sample, 'BILL-0001')
date = extract_field(r'Date.*?<td[^>]*class=["\']value["\'][^>]*>(.*?)</td>', html_sample, '15/08/2026')
tenant = extract_field(r'Tenant Name.*?<td[^>]*class=["\']value["\'][^>]*>(.*?)</td>', html_sample, 'Tenant')
house = extract_field(r'Rent of Gala/House No\..*?<td[^>]*class=["\']value["\'][^>]*>(.*?)</td>', html_sample, 'Gala')
month = extract_field(r'Month.*?<td[^>]*class=["\']value["\'][^>]*>(.*?)</td>', html_sample, 'Month')
amount = extract_field(r'Rs\.\s*([\d\.]+)', html_sample, '0.00')

print('Extracted:', bill_no, date, tenant, house, month, amount)
