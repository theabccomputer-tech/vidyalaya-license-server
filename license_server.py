"""
╔══════════════════════════════════════════════════════════════╗
║      VIDYALAYA PRO — ONLINE LICENSE SERVER                   ║
║      Deploy on Render.com (Free Plan)                        ║
║      Developer: Saadat  |  All Rights Reserved               ║
╚══════════════════════════════════════════════════════════════╝

Render pe deploy karne ke liye:
  1. Is folder ko alag GitHub repo mein daalo
  2. Render > New Web Service > Connect repo
  3. Environment variables set karo (neeche dekho)
  4. Deploy!
"""

from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, date
import os, hashlib, hmac, json
import smtplib
from email.mime.text import MIMEText

app = Flask(__name__)

# ── Database (Render pe PostgreSQL ya SQLite) ──────────────────
DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///licenses.db')
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# ── Environment Variables (Render mein set karna) ──────────────
ADMIN_SECRET   = os.getenv('ADMIN_SECRET', 'SAADAT_ADMIN_2025')   # Admin panel password
SERVER_SECRET  = os.getenv('SERVER_SECRET', 'VIDYA_SERVER_KEY_XYZ') # API hmac signing
GMAIL_USER     = os.getenv('GMAIL_USER', '')                        # tera Gmail
GMAIL_PASS     = os.getenv('GMAIL_PASS', '')                        # Gmail App Password
NOTIFY_EMAIL   = os.getenv('NOTIFY_EMAIL', '')                      # tujhe notification kahan aaye

MAX_FREE_REACTIVATIONS = 2   # Isse zyada pe ₹500 charge

# ══════════════════════════════════════════════════════════════
# DATABASE MODELS
# ══════════════════════════════════════════════════════════════

class License(db.Model):
    __tablename__ = 'license'
    id                  = db.Column(db.Integer, primary_key=True)
    key                 = db.Column(db.String(100), unique=True, nullable=False)
    school_name         = db.Column(db.String(200))
    school_city         = db.Column(db.String(100))
    contact_person      = db.Column(db.String(100), default='')
    contact_phone       = db.Column(db.String(20),  default='')
    max_students        = db.Column(db.Integer, default=500)
    expiry_date         = db.Column(db.String(20))       # YYYY-MM-DD
    machine_id          = db.Column(db.String(200), default='')  # active machine
    activation_count    = db.Column(db.Integer, default=0)
    reactivation_count  = db.Column(db.Integer, default=0)
    reactivation_year   = db.Column(db.Integer, default=0)  # year track ke liye reset
    status              = db.Column(db.String(20), default='active')  # active/suspended/pending
    created_at          = db.Column(db.DateTime, default=datetime.utcnow)
    last_seen           = db.Column(db.DateTime, default=datetime.utcnow)
    notes               = db.Column(db.Text, default='')

class ReactivationRequest(db.Model):
    __tablename__ = 'reactivation_request'
    id              = db.Column(db.Integer, primary_key=True)
    license_key     = db.Column(db.String(100))
    school_name     = db.Column(db.String(200))
    old_machine_id  = db.Column(db.String(200))
    new_machine_id  = db.Column(db.String(200))
    reason          = db.Column(db.Text, default='')
    reactivation_no = db.Column(db.Integer, default=1)  # kaunwa reactivation hai
    status          = db.Column(db.String(20), default='pending')  # pending/approved/rejected/payment_pending
    payment_done    = db.Column(db.Boolean, default=False)
    requested_at    = db.Column(db.DateTime, default=datetime.utcnow)
    resolved_at     = db.Column(db.DateTime, nullable=True)

# ══════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ══════════════════════════════════════════════════════════════

def send_email_notification(subject, body):
    """Saadat ko email notification bhejo"""
    if not GMAIL_USER or not GMAIL_PASS or not NOTIFY_EMAIL:
        print(f"[EMAIL SKIP] {subject}")
        return
    try:
        msg = MIMEText(body, 'plain', 'utf-8')
        msg['Subject'] = f"[VidyalayaPro] {subject}"
        msg['From']    = GMAIL_USER
        msg['To']      = NOTIFY_EMAIL
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(GMAIL_USER, GMAIL_PASS)
            smtp.send_message(msg)
        print(f"[EMAIL SENT] {subject}")
    except Exception as e:
        print(f"[EMAIL ERROR] {e}")

def verify_request_signature(data, signature):
    """Request tamper nahi hui check karo"""
    payload = json.dumps(data, sort_keys=True)
    expected = hmac.new(SERVER_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)

def reset_reactivation_count_if_new_year(lic):
    """Naya saal aaya to reactivation count reset karo"""
    current_year = date.today().year
    if lic.reactivation_year != current_year:
        lic.reactivation_year  = current_year
        lic.reactivation_count = 0
        db.session.commit()

# ══════════════════════════════════════════════════════════════
# API ROUTES — School ka App inhein call karta hai
# ══════════════════════════════════════════════════════════════

@app.route('/api/activate', methods=['POST'])
def api_activate():
    """
    Pehli baar activation ya normal startup check.
    School ka app har startup pe ye call karta hai.
    """
    data = request.get_json() or {}
    key        = data.get('key', '').strip().upper()
    machine_id = data.get('machine_id', '').strip()

    if not key or not machine_id:
        return jsonify({'valid': False, 'message': '❌ Key ya Machine ID missing hai.'})

    lic = License.query.filter_by(key=key).first()

    # Key exist nahi karti
    if not lic:
        return jsonify({'valid': False, 'message': '❌ License key galat hai. Developer se sampark karein.'})

    # Suspended
    if lic.status == 'suspended':
        return jsonify({'valid': False, 'message': '❌ License suspend hai. Developer se sampark karein.'})

    # Expiry check
    if lic.expiry_date:
        expiry = datetime.strptime(lic.expiry_date, '%Y-%m-%d')
        if datetime.now() > expiry:
            days_ago = (datetime.now() - expiry).days
            return jsonify({'valid': False,
                'message': f'❌ License {days_ago} din pehle expire ho gayi. Renewal ke liye sampark karein.'})

    # Pehli baar activation — machine ID set karo
    if not lic.machine_id:
        lic.machine_id       = machine_id
        lic.activation_count = 1
        lic.last_seen        = datetime.utcnow()
        db.session.commit()

        send_email_notification(
            f"New Activation: {lic.school_name}",
            f"School: {lic.school_name}\nCity: {lic.school_city}\n"
            f"Machine ID: {machine_id}\nTime: {datetime.now()}"
        )
        days_left = (datetime.strptime(lic.expiry_date, '%Y-%m-%d') - datetime.now()).days
        return jsonify({
            'valid': True,
            'message': f'✅ License activate ho gayi! {days_left} din baaki.',
            'school_name': lic.school_name,
            'max_students': lic.max_students,
            'expiry': lic.expiry_date,
            'days_left': days_left
        })

    # Same machine — normal startup
    if lic.machine_id == machine_id:
        lic.last_seen = datetime.utcnow()
        db.session.commit()
        days_left = (datetime.strptime(lic.expiry_date, '%Y-%m-%d') - datetime.now()).days
        return jsonify({
            'valid': True,
            'message': f'✅ License valid. {days_left} din baaki.',
            'school_name': lic.school_name,
            'max_students': lic.max_students,
            'expiry': lic.expiry_date,
            'days_left': days_left
        })

    # Alag machine — reactivation chahiye
    return jsonify({
        'valid': False,
        'needs_reactivation': True,
        'message': '⚠️ Ye license kisi aur machine pe active hai. Reactivation ke liye request karo.'
    })


@app.route('/api/reactivate', methods=['POST'])
def api_reactivate():
    """
    School PC badal gaya ya format hua — reactivation request.
    """
    data = request.get_json() or {}
    key            = data.get('key', '').strip().upper()
    new_machine_id = data.get('new_machine_id', '').strip()
    reason         = data.get('reason', '').strip()

    if not key or not new_machine_id:
        return jsonify({'success': False, 'message': 'Key ya Machine ID missing.'})

    lic = License.query.filter_by(key=key).first()
    if not lic:
        return jsonify({'success': False, 'message': '❌ License key nahi mili.'})

    if lic.status == 'suspended':
        return jsonify({'success': False, 'message': '❌ License suspend hai.'})

    # Same machine pe reactivation request — koi zaroorat nahi
    if lic.machine_id == new_machine_id:
        return jsonify({'success': False, 'message': 'Ye machine already active hai!'})

    # Pehle se pending request hai?
    existing = ReactivationRequest.query.filter_by(
        license_key=key, status='pending').first()
    if existing:
        return jsonify({
            'success': True,
            'status': 'already_pending',
            'message': '⏳ Aapki request pehle se pending hai. Developer approve karega 24 ghante mein.'
        })

    # Is saal kitni reactivations ho chuki hain
    reset_reactivation_count_if_new_year(lic)
    react_no = lic.reactivation_count + 1

    # 3rd+ reactivation — payment required
    if react_no > MAX_FREE_REACTIVATIONS:
        req = ReactivationRequest(
            license_key    = key,
            school_name    = lic.school_name,
            old_machine_id = lic.machine_id,
            new_machine_id = new_machine_id,
            reason         = reason,
            reactivation_no= react_no,
            status         = 'payment_pending'
        )
        db.session.add(req)
        db.session.commit()

        send_email_notification(
            f"💰 PAID Reactivation #{react_no}: {lic.school_name}",
            f"School: {lic.school_name}\nCity: {lic.school_city}\n"
            f"Reactivation No: {react_no} (PAID - ₹500)\n"
            f"Old Machine: {lic.machine_id}\nNew Machine: {new_machine_id}\n"
            f"Reason: {reason}\n\nAdmin Panel: https://vidyalaya-license.onrender.com/admin"
        )
        return jsonify({
            'success': True,
            'status': 'payment_required',
            'message': f'💰 Yah aapka {react_no}wa reactivation hai. ₹500 payment ke baad activate hoga.\n'
                       f'Payment karo aur developer ko WhatsApp karo: reactivation request #{req.id}'
        })

    # Free reactivation (1st ya 2nd)
    req = ReactivationRequest(
        license_key    = key,
        school_name    = lic.school_name,
        old_machine_id = lic.machine_id,
        new_machine_id = new_machine_id,
        reason         = reason,
        reactivation_no= react_no,
        status         = 'pending'
    )
    db.session.add(req)
    db.session.commit()

    send_email_notification(
        f"🔄 Reactivation Request #{react_no}: {lic.school_name}",
        f"School: {lic.school_name}\nCity: {lic.school_city}\n"
        f"Reactivation No: {react_no}/{MAX_FREE_REACTIVATIONS} free\n"
        f"Old Machine: {lic.machine_id}\nNew Machine: {new_machine_id}\n"
        f"Reason: {reason}\n\n"
        f"Admin Panel pe approve karo:\nhttps://vidyalaya-license.onrender.com/admin"
    )

    return jsonify({
        'success': True,
        'status': 'pending',
        'request_id': req.id,
        'message': f'✅ Request submit ho gayi! ({react_no}/{MAX_FREE_REACTIVATIONS} free)\n'
                   f'Developer 24 ghante mein approve karega. Tab tak app band rahegi.'
    })


@app.route('/api/check-reactivation', methods=['POST'])
def api_check_reactivation():
    """
    School ka app poll karta hai — kya reactivation approve hua?
    """
    data = request.get_json() or {}
    key            = data.get('key', '').strip().upper()
    new_machine_id = data.get('new_machine_id', '').strip()

    lic = License.query.filter_by(key=key).first()
    if not lic:
        return jsonify({'approved': False, 'message': 'License nahi mili.'})

    # Check karo approved request
    req = ReactivationRequest.query.filter_by(
        license_key=key, new_machine_id=new_machine_id,
        status='approved').first()

    if req:
        days_left = (datetime.strptime(lic.expiry_date, '%Y-%m-%d') - datetime.now()).days
        return jsonify({
            'approved': True,
            'message': f'✅ Reactivation approve ho gaya! {days_left} din baaki.',
            'school_name': lic.school_name,
            'max_students': lic.max_students,
            'expiry': lic.expiry_date,
            'days_left': days_left
        })

    # Pending check
    pending = ReactivationRequest.query.filter_by(
        license_key=key, new_machine_id=new_machine_id,
        status='pending').first()
    if pending:
        return jsonify({'approved': False, 'status': 'pending',
                        'message': '⏳ Request pending hai. 24 ghante mein approve hoga.'})

    payment_pending = ReactivationRequest.query.filter_by(
        license_key=key, new_machine_id=new_machine_id,
        status='payment_pending').first()
    if payment_pending:
        return jsonify({'approved': False, 'status': 'payment_pending',
                        'message': f'💰 Request #{payment_pending.id} ke liye ₹500 payment karo.'})

    return jsonify({'approved': False, 'status': 'not_found',
                    'message': 'Koi pending request nahi mili.'})


# ══════════════════════════════════════════════════════════════
# ADMIN PANEL — Saadat ke liye
# ══════════════════════════════════════════════════════════════

@app.route('/admin')
def admin_panel():
    secret = request.args.get('secret', '')
    if secret != ADMIN_SECRET:
        return '''
        <html><body style="font-family:Arial;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;background:#1e1e2e">
        <div style="background:white;padding:40px;border-radius:16px;text-align:center;min-width:320px">
          <h2 style="margin:0 0 20px">🔐 Admin Login</h2>
          <form method="GET">
            <input name="secret" type="password" placeholder="Admin Password"
              style="width:100%;padding:10px;border:1px solid #ddd;border-radius:8px;font-size:1rem;margin-bottom:12px;box-sizing:border-box">
            <button type="submit"
              style="width:100%;padding:10px;background:#6c63ff;color:white;border:none;border-radius:8px;font-size:1rem;cursor:pointer">Login</button>
          </form>
        </div></body></html>
        ''', 200

    pending = ReactivationRequest.query.filter_by(status='pending').order_by(
        ReactivationRequest.requested_at.desc()).all()
    payment_pending = ReactivationRequest.query.filter_by(status='payment_pending').order_by(
        ReactivationRequest.requested_at.desc()).all()
    all_licenses = License.query.order_by(License.created_at.desc()).all()

    pending_html = ''
    for r in pending:
        pending_html += f'''
        <div style="background:#fff8e1;border:1px solid #f59e0b;border-radius:12px;padding:16px;margin-bottom:12px">
          <div style="display:flex;justify-content:space-between;align-items:start">
            <div>
              <strong>🏫 {r.school_name}</strong> &nbsp;|&nbsp; Request #{r.id}<br>
              <small style="color:#666">Reactivation #{r.reactivation_no}/2 FREE</small><br>
              <small>Reason: {r.reason or 'Not specified'}</small><br>
              <small style="color:#999">Requested: {r.requested_at.strftime('%d %b %Y %H:%M')}</small>
            </div>
            <div style="display:flex;gap:8px">
              <a href="/admin/approve/{r.id}?secret={secret}"
                style="background:#10b981;color:white;padding:8px 16px;border-radius:8px;text-decoration:none;font-weight:700">✅ Approve</a>
              <a href="/admin/reject/{r.id}?secret={secret}"
                style="background:#ef4444;color:white;padding:8px 16px;border-radius:8px;text-decoration:none;font-weight:700">❌ Reject</a>
            </div>
          </div>
        </div>'''

    payment_html = ''
    for r in payment_pending:
        payment_html += f'''
        <div style="background:#fff0f0;border:1px solid #ef4444;border-radius:12px;padding:16px;margin-bottom:12px">
          <div style="display:flex;justify-content:space-between;align-items:start">
            <div>
              <strong>🏫 {r.school_name}</strong> &nbsp;|&nbsp; Request #{r.id}<br>
              <small style="color:#ef4444">💰 Reactivation #{r.reactivation_no} — ₹500 Required</small><br>
              <small>Reason: {r.reason or 'Not specified'}</small><br>
              <small style="color:#999">Requested: {r.requested_at.strftime('%d %b %Y %H:%M')}</small>
            </div>
            <div style="display:flex;gap:8px">
              <a href="/admin/approve/{r.id}?secret={secret}&paid=1"
                style="background:#10b981;color:white;padding:8px 16px;border-radius:8px;text-decoration:none;font-weight:700">✅ Payment Mila, Approve</a>
              <a href="/admin/reject/{r.id}?secret={secret}"
                style="background:#ef4444;color:white;padding:8px 16px;border-radius:8px;text-decoration:none;font-weight:700">❌ Reject</a>
            </div>
          </div>
        </div>'''

    licenses_html = ''
    for lic in all_licenses:
        status_color = '#10b981' if lic.status == 'active' else '#ef4444'
        licenses_html += f'''
        <tr style="border-bottom:1px solid #eee">
          <td style="padding:10px">{lic.school_name}</td>
          <td style="padding:10px">{lic.school_city}</td>
          <td style="padding:10px;font-family:monospace;font-size:.75rem">{lic.key[:25]}...</td>
          <td style="padding:10px">{lic.max_students}</td>
          <td style="padding:10px">{lic.expiry_date}</td>
          <td style="padding:10px">{lic.reactivation_count}/{MAX_FREE_REACTIVATIONS}</td>
          <td style="padding:10px"><span style="color:{status_color};font-weight:700">{lic.status.upper()}</span></td>
          <td style="padding:10px">
            <a href="/admin/suspend/{lic.id}?secret={secret}"
              style="color:#ef4444;font-size:.75rem;font-weight:700">Suspend</a>
          </td>
        </tr>'''

    return f'''
    <!DOCTYPE html><html><head>
    <meta charset="UTF-8">
    <title>VidyalayaPro License Admin</title>
    <style>body{{font-family:Arial,sans-serif;background:#f0f2f5;margin:0}}
    .hdr{{background:linear-gradient(135deg,#6c63ff,#8e85ff);color:white;padding:20px 32px}}
    .hdr h1{{margin:0;font-size:1.4rem}}.hdr p{{margin:4px 0 0;opacity:.8;font-size:.85rem}}
    .container{{max-width:1100px;margin:0 auto;padding:24px}}
    .section{{background:white;border-radius:16px;padding:20px;margin-bottom:20px;box-shadow:0 2px 8px rgba(0,0,0,.06)}}
    .section h2{{margin:0 0 16px;font-size:1rem;color:#333}}
    table{{width:100%;border-collapse:collapse}}th{{background:#f8f9fa;padding:10px;text-align:left;font-size:.8rem;color:#666}}
    .badge{{display:inline-block;padding:3px 10px;border-radius:20px;font-size:.72rem;font-weight:700}}
    .stat-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:20px}}
    .stat{{background:white;border-radius:12px;padding:16px;text-align:center;box-shadow:0 2px 8px rgba(0,0,0,.06)}}
    .stat-n{{font-size:2rem;font-weight:900;color:#6c63ff}}.stat-l{{font-size:.78rem;color:#666;margin-top:4px}}
    </style></head><body>
    <div class="hdr">
      <h1>🎓 VidyalayaPro License Admin Panel</h1>
      <p>Saadat — Developer Dashboard</p>
    </div>
    <div class="container">

      <!-- Stats -->
      <div class="stat-grid">
        <div class="stat"><div class="stat-n">{License.query.count()}</div><div class="stat-l">Total Licenses</div></div>
        <div class="stat"><div class="stat-n">{License.query.filter_by(status='active').count()}</div><div class="stat-l">Active Schools</div></div>
        <div class="stat"><div class="stat-n" style="color:#f59e0b">{len(pending)}</div><div class="stat-l">Pending Requests</div></div>
        <div class="stat"><div class="stat-n" style="color:#ef4444">{len(payment_pending)}</div><div class="stat-l">Payment Pending</div></div>
      </div>

      <!-- Pending Approvals -->
      <div class="section">
        <h2>🔄 Free Reactivation Requests {f"({len(pending)})" if pending else ""}</h2>
        {pending_html if pending else '<p style="color:#999;text-align:center;padding:20px">Koi pending request nahi hai ✅</p>'}
      </div>

      <!-- Payment Pending -->
      <div class="section">
        <h2>💰 Paid Reactivation Requests {f"({len(payment_pending)})" if payment_pending else ""}</h2>
        {payment_html if payment_pending else '<p style="color:#999;text-align:center;padding:20px">Koi payment pending nahi ✅</p>'}
      </div>

      <!-- All Licenses -->
      <div class="section">
        <h2>📋 All Licenses</h2>
        <div style="overflow-x:auto">
        <table>
          <tr>
            <th>School</th><th>City</th><th>Key</th><th>Students</th>
            <th>Expiry</th><th>Reactivations</th><th>Status</th><th>Action</th>
          </tr>
          {licenses_html}
        </table>
        </div>
      </div>

      <!-- New License Form -->
      <div class="section">
        <h2>➕ Naya License Banao</h2>
        <form method="POST" action="/admin/create-license?secret={secret}"
          style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
          <div><label style="font-size:.8rem;font-weight:700">School Name</label><br>
            <input name="school_name" required style="width:100%;padding:8px;border:1px solid #ddd;border-radius:8px;margin-top:4px;box-sizing:border-box"></div>
          <div><label style="font-size:.8rem;font-weight:700">City</label><br>
            <input name="school_city" required style="width:100%;padding:8px;border:1px solid #ddd;border-radius:8px;margin-top:4px;box-sizing:border-box"></div>
          <div><label style="font-size:.8rem;font-weight:700">Contact Person</label><br>
            <input name="contact_person" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:8px;margin-top:4px;box-sizing:border-box"></div>
          <div><label style="font-size:.8rem;font-weight:700">Phone</label><br>
            <input name="contact_phone" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:8px;margin-top:4px;box-sizing:border-box"></div>
          <div><label style="font-size:.8rem;font-weight:700">Max Students</label><br>
            <select name="max_students" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:8px;margin-top:4px">
              <option value="200">200 Students</option>
              <option value="500" selected>500 Students</option>
              <option value="1000">1000 Students</option>
              <option value="99999">Unlimited</option>
            </select></div>
          <div><label style="font-size:.8rem;font-weight:700">Validity</label><br>
            <select name="days_valid" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:8px;margin-top:4px">
              <option value="90">3 Months</option>
              <option value="180">6 Months</option>
              <option value="365" selected>1 Year</option>
              <option value="730">2 Years</option>
            </select></div>
          <div style="grid-column:1/-1">
            <label style="font-size:.8rem;font-weight:700">Notes (optional)</label><br>
            <input name="notes" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:8px;margin-top:4px;box-sizing:border-box"></div>
          <div style="grid-column:1/-1">
            <button type="submit"
              style="background:#6c63ff;color:white;padding:10px 32px;border:none;border-radius:10px;font-weight:700;font-size:.95rem;cursor:pointer">
              🔑 License Generate Karo
            </button>
          </div>
        </form>
      </div>

    </div></body></html>
    '''


@app.route('/admin/approve/<int:req_id>', methods=['GET'])
def admin_approve(req_id):
    secret = request.args.get('secret', '')
    if secret != ADMIN_SECRET:
        return 'Unauthorized', 403

    req = ReactivationRequest.query.get_or_404(req_id)
    lic = License.query.filter_by(key=req.license_key).first()

    if not lic:
        return 'License nahi mili', 404

    # Machine ID update karo
    lic.machine_id          = req.new_machine_id
    lic.reactivation_count += 1
    lic.last_seen           = datetime.utcnow()

    req.status      = 'approved'
    req.resolved_at = datetime.utcnow()
    if request.args.get('paid'):
        req.payment_done = True

    db.session.commit()

    send_email_notification(
        f"✅ Approved: {lic.school_name}",
        f"Reactivation #{req.reactivation_no} approved for {lic.school_name}.\n"
        f"New Machine: {req.new_machine_id}"
    )

    return f'''<html><body style="font-family:Arial;text-align:center;padding:60px;background:#f0f2f5">
    <div style="background:white;border-radius:16px;padding:40px;max-width:400px;margin:0 auto">
      <div style="font-size:3rem">✅</div>
      <h2 style="color:#10b981">{lic.school_name}</h2>
      <p>Reactivation #{req.reactivation_no} approve ho gaya!<br>
      School ka app ab naye PC pe chalega.</p>
      <a href="/admin?secret={secret}"
        style="background:#6c63ff;color:white;padding:10px 24px;border-radius:10px;text-decoration:none;font-weight:700">
        ← Admin Panel</a>
    </div></body></html>'''


@app.route('/admin/reject/<int:req_id>')
def admin_reject(req_id):
    secret = request.args.get('secret', '')
    if secret != ADMIN_SECRET:
        return 'Unauthorized', 403

    req = ReactivationRequest.query.get_or_404(req_id)
    req.status      = 'rejected'
    req.resolved_at = datetime.utcnow()
    db.session.commit()

    return f'''<html><body style="font-family:Arial;text-align:center;padding:60px;background:#f0f2f5">
    <div style="background:white;border-radius:16px;padding:40px;max-width:400px;margin:0 auto">
      <div style="font-size:3rem">❌</div>
      <h2>Request Reject Ho Gayi</h2>
      <a href="/admin?secret={secret}"
        style="background:#6c63ff;color:white;padding:10px 24px;border-radius:10px;text-decoration:none;font-weight:700">
        ← Admin Panel</a>
    </div></body></html>'''


@app.route('/admin/suspend/<int:lic_id>')
def admin_suspend(lic_id):
    secret = request.args.get('secret', '')
    if secret != ADMIN_SECRET:
        return 'Unauthorized', 403
    lic = License.query.get_or_404(lic_id)
    lic.status = 'suspended' if lic.status == 'active' else 'active'
    db.session.commit()
    return f'<script>window.location="/admin?secret={secret}"</script>'


@app.route('/admin/create-license', methods=['POST'])
def admin_create_license():
    secret = request.args.get('secret', '')
    if secret != ADMIN_SECRET:
        return 'Unauthorized', 403

    from datetime import timedelta
    import random, string

    f            = request.form
    school_name  = f['school_name'].strip()
    school_city  = f['school_city'].strip()
    max_students = int(f.get('max_students', 500))
    days_valid   = int(f.get('days_valid', 365))
    contact      = f.get('contact_person', '')
    phone        = f.get('contact_phone', '')
    notes        = f.get('notes', '')

    expiry = (datetime.now() + timedelta(days=days_valid)).strftime('%Y-%m-%d')

    # Key generate
    school_code = hashlib.md5(f"{school_name}{school_city}".encode()).hexdigest()[:6].upper()
    rand_part   = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    checksum    = hashlib.sha256(f"{school_name}|{school_city}|{expiry}|{max_students}|SAADAT2025".encode()).hexdigest()[:8].upper()
    key         = f"VIDYA-{school_code}-{rand_part}-{max_students}-{checksum}"

    lic = License(
        key=key, school_name=school_name, school_city=school_city,
        contact_person=contact, contact_phone=phone,
        max_students=max_students, expiry_date=expiry,
        reactivation_year=date.today().year, notes=notes
    )
    db.session.add(lic)
    db.session.commit()

    send_email_notification(
        f"🔑 New License Created: {school_name}",
        f"School: {school_name}\nCity: {school_city}\nStudents: {max_students}\n"
        f"Expiry: {expiry}\nKey: {key}"
    )

    return f'''<html><body style="font-family:Arial;text-align:center;padding:60px;background:#f0f2f5">
    <div style="background:white;border-radius:16px;padding:40px;max-width:500px;margin:0 auto">
      <div style="font-size:2.5rem">🔑</div>
      <h2 style="color:#6c63ff">{school_name}</h2>
      <p style="color:#666">License generate ho gayi!</p>
      <div style="background:#f8f9fa;border:2px dashed #6c63ff;border-radius:12px;padding:16px;margin:20px 0;word-break:break-all;font-family:monospace;font-size:.9rem;font-weight:700;color:#1e1e2e">
        {key}
      </div>
      <p style="font-size:.85rem;color:#666">
        Expiry: <strong>{expiry}</strong> &nbsp;|&nbsp;
        Students: <strong>{max_students}</strong>
      </p>
      <p style="font-size:.8rem;color:#f59e0b">⚠️ Ye key school ko do. Settings > License mein paste karein.</p>
      <a href="/admin?secret={secret}"
        style="background:#6c63ff;color:white;padding:10px 24px;border-radius:10px;text-decoration:none;font-weight:700">
        ← Admin Panel</a>
    </div></body></html>'''


# Health check
@app.route('/ping')
def ping():
    return jsonify({'status': 'ok', 'service': 'VidyalayaPro License Server'})


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        print("✅ Database tables create ho gayi")
    app.run(debug=False, host='0.0.0.0', port=int(os.getenv('PORT', 5001)))
