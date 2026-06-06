"""
╔══════════════════════════════════════════════════════════════╗
║         VIDYALAYA PRO - LICENSE MANAGER (v2.0)              ║
║         Online Server + Machine Lock + Offline Cache         ║
║         Developer: Saadat  |  All Rights Reserved           ║
╚══════════════════════════════════════════════════════════════╝
"""
import hashlib, json, os, platform, uuid, subprocess
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

# ── Config ─────────────────────────────────────────────────────
LICENSE_SERVER   = os.getenv('LICENSE_SERVER_URL',
                             'https://vidyalaya-license.onrender.com')
OFFLINE_CACHE_H  = 48          # Kitne ghante offline chale (internet nahi to)
LICENSE_KEY_FILE = os.path.join(os.path.dirname(__file__), 'license.key')
CACHE_FILE       = os.path.join(os.path.dirname(__file__), '.lic_cache')
MASTER_KEY       = os.getenv('MASTER_KEY', 'VIDYA-MASTER-SAADAT-2025')

# ══════════════════════════════════════════════════════════════
# MACHINE ID — Is PC ki unique pehchaan
# ══════════════════════════════════════════════════════════════

def get_machine_id() -> str:
    """
    Is computer ki unique ID banao (CPU + Disk se).
    Format/reinstall pe bhi same rehti hai.
    """
    try:
        parts = []

        if platform.system() == 'Windows':
            # CPU ID
            try:
                cpu = subprocess.check_output(
                    'wmic cpu get ProcessorId',
                    shell=True, stderr=subprocess.DEVNULL
                ).decode().split('\n')[1].strip()
                if cpu: parts.append(cpu)
            except: pass

            # Motherboard serial
            try:
                mb = subprocess.check_output(
                    'wmic baseboard get SerialNumber',
                    shell=True, stderr=subprocess.DEVNULL
                ).decode().split('\n')[1].strip()
                if mb and mb != 'To be filled by O.E.M.': parts.append(mb)
            except: pass

            # Disk serial
            try:
                disk = subprocess.check_output(
                    'wmic diskdrive get SerialNumber',
                    shell=True, stderr=subprocess.DEVNULL
                ).decode().split('\n')[1].strip()
                if disk: parts.append(disk)
            except: pass

        elif platform.system() == 'Linux':
            try:
                with open('/etc/machine-id') as f:
                    parts.append(f.read().strip())
            except: pass

        elif platform.system() == 'Darwin':  # Mac
            try:
                out = subprocess.check_output(
                    ['ioreg', '-l', '-n', 'AppleSmartBattery'],
                    stderr=subprocess.DEVNULL
                ).decode()
                for line in out.split('\n'):
                    if 'Serial' in line:
                        parts.append(line.split('=')[-1].strip().strip('"'))
                        break
            except: pass

        if not parts:
            # Fallback: network MAC address
            mac = ':'.join(['{:02x}'.format((uuid.getnode() >> i) & 0xff)
                           for i in range(0, 8*6, 8)][::-1])
            parts.append(mac)

        raw = '|'.join(parts)
        return hashlib.sha256(raw.encode()).hexdigest()[:32].upper()

    except Exception as e:
        # Last resort fallback
        return hashlib.sha256(platform.node().encode()).hexdigest()[:32].upper()

# ══════════════════════════════════════════════════════════════
# LICENSE KEY FILE — Read/Write
# ══════════════════════════════════════════════════════════════

def load_license() -> str:
    if not os.path.exists(LICENSE_KEY_FILE):
        return ''
    try:
        with open(LICENSE_KEY_FILE) as f:
            return f.read().strip()
    except:
        return ''

def save_license(key: str):
    try:
        with open(LICENSE_KEY_FILE, 'w') as f:
            f.write(key.strip())
    except Exception as e:
        print(f"[LICENSE] Key save error: {e}")

# ══════════════════════════════════════════════════════════════
# OFFLINE CACHE — 48 ghante internet nahi to bhi chale
# ══════════════════════════════════════════════════════════════

def _save_cache(data: dict):
    try:
        data['cached_at'] = datetime.now().isoformat()
        with open(CACHE_FILE, 'w') as f:
            json.dump(data, f)
    except: pass

def _load_cache() -> dict | None:
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE) as f:
            data = json.load(f)
        cached_at = datetime.fromisoformat(data.get('cached_at', '2000-01-01'))
        if datetime.now() - cached_at < timedelta(hours=OFFLINE_CACHE_H):
            return data
        return None   # Cache expire
    except:
        return None

# ══════════════════════════════════════════════════════════════
# MASTER KEY CHECK — Sirf developer ke liye
# ══════════════════════════════════════════════════════════════

def _is_master_key(key: str) -> bool:
    return key.strip().upper() == MASTER_KEY.strip().upper()

# ══════════════════════════════════════════════════════════════
# MAIN VERIFY FUNCTION — App.py yahi call karta hai
# ══════════════════════════════════════════════════════════════

def verify_license(license_key: str) -> dict:
    """
    License verify karo.
    1. Master key? → hamesha valid
    2. Online server se check karo
    3. Server nahi mila? → offline cache use karo (48h)
    4. Cache bhi nahi? → invalid
    """
    key = license_key.strip().upper() if license_key else ''

    if not key:
        return _invalid('License key nahi mili. Settings mein activate karein.')

    # ── Master key ──
    if _is_master_key(key):
        return {
            'valid': True, 'master': True,
            'data': {'school': 'Master Admin', 'expiry': '9999-12-31', 'students': 99999},
            'message': '✅ Master License Active'
        }

    machine_id = get_machine_id()

    # ── Online server se check karo ──
    try:
        import urllib.request, json as _json
        payload = _json.dumps({'key': key, 'machine_id': machine_id}).encode()
        req = urllib.request.Request(
            f'{LICENSE_SERVER}/api/activate',
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            result = _json.loads(resp.read().decode())

        if result.get('valid'):
            # Cache save karo
            _save_cache(result)
            days_left = result.get('days_left', 0)
            return {
                'valid': True, 'master': False,
                'data': {
                    'school':   result.get('school_name', ''),
                    'expiry':   result.get('expiry', ''),
                    'students': result.get('max_students', 500)
                },
                'message': f"✅ License Valid | {days_left} din baaki | Max {result.get('max_students', 500)} students"
            }
        elif result.get('needs_reactivation'):
            return {
                'valid': False,
                'needs_reactivation': True,
                'machine_id': machine_id,
                'message': result.get('message', 'Reactivation required.')
            }
        else:
            return _invalid(result.get('message', 'License invalid hai.'))

    except Exception as e:
        # ── Server nahi mila — offline cache try karo ──
        cache = _load_cache()
        if cache and cache.get('valid'):
            cached_at = datetime.fromisoformat(cache.get('cached_at', '2000-01-01'))
            hours_left = OFFLINE_CACHE_H - int((datetime.now() - cached_at).total_seconds() / 3600)
            return {
                'valid': True, 'master': False, 'offline': True,
                'data': {
                    'school':   cache.get('school_name', ''),
                    'expiry':   cache.get('expiry', ''),
                    'students': cache.get('max_students', 500)
                },
                'message': f"✅ Offline Mode | Cache {hours_left}h baaki | Internet se connect karo"
            }

        return _invalid(f'Server se connect nahi hua aur cache nahi mili. ({str(e)[:50]})')

def _invalid(message: str) -> dict:
    return {'valid': False, 'data': {}, 'message': f'❌ {message}'}

# ══════════════════════════════════════════════════════════════
# REACTIVATION — Naye PC pe move karna
# ══════════════════════════════════════════════════════════════

def request_reactivation(license_key: str, reason: str) -> dict:
    """
    School ne PC badla — reactivation request server ko bhejo.
    """
    key        = license_key.strip().upper()
    machine_id = get_machine_id()

    try:
        import urllib.request, json as _json
        payload = _json.dumps({
            'key':            key,
            'new_machine_id': machine_id,
            'reason':         reason
        }).encode()
        req = urllib.request.Request(
            f'{LICENSE_SERVER}/api/reactivate',
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return _json.loads(resp.read().decode())
    except Exception as e:
        return {'success': False, 'message': f'Server se connect nahi hua: {str(e)[:60]}'}

def check_reactivation_status(license_key: str) -> dict:
    """
    Poll karo — kya reactivation approve hua?
    """
    key        = license_key.strip().upper()
    machine_id = get_machine_id()

    try:
        import urllib.request, json as _json
        payload = _json.dumps({
            'key':            key,
            'new_machine_id': machine_id
        }).encode()
        req = urllib.request.Request(
            f'{LICENSE_SERVER}/api/check-reactivation',
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            result = _json.loads(resp.read().decode())

        if result.get('approved'):
            # Cache update karo
            _save_cache(result)
        return result
    except Exception as e:
        return {'approved': False, 'message': f'Server nahi mila: {str(e)[:60]}'}

# ══════════════════════════════════════════════════════════════
# STARTUP CHECK — app.py start pe yahi call hota hai
# ══════════════════════════════════════════════════════════════

def check_on_startup() -> dict:
    key = load_license()
    if not key:
        return _invalid('License nahi mili. Settings mein activate karein.')
    return verify_license(key)

# ══════════════════════════════════════════════════════════════
# COMMAND LINE TOOL
# ══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    print("\n" + "="*55)
    print("   VIDYALAYA PRO — LICENSE MANAGER v2.0")
    print("   Developer: Saadat")
    print("="*55)
    print(f"\n🖥️  Is PC ki Machine ID:")
    print(f"   {get_machine_id()}")
    print(f"\n📄 Saved License Key:")
    key = load_license()
    print(f"   {key if key else '(koi key nahi)'}")

    if key:
        print(f"\n🔍 License Check karo...")
        result = verify_license(key)
        print(f"   {result['message']}")
        if result.get('needs_reactivation'):
            print(f"\n⚠️  Reactivation chahiye!")
            reason = input("   Reason batao (e.g. PC badla, format hua): ").strip()
            if reason:
                r = request_reactivation(key, reason)
                print(f"   {r.get('message', 'Error')}")

    print("\n" + "="*55)
    input("Enter dabao band karne ke liye...")
