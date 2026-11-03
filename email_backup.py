import os, shutil, gzip, smtplib, datetime
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders

def _get_email_to():
    try:
        import database as _db
        s = _db.query_one("SELECT * FROM backup_settings WHERE company_id=1")
        if s:
            if s.get("email_enabled") == 0:
                return None
            t = (s.get("email_to") or "").strip()
            if t:
                return t
    except Exception:
        pass
    return os.getenv("EMAIL_TO")

def send_backup_email():
    db_path = Path('instance/accounting.db')
    if not db_path.exists(): return False, 'database not found'
    backup_dir = Path('backups/auto')
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    tmp_db = backup_dir / ('auto_' + ts + '.db')
    shutil.copy2(str(db_path), str(tmp_db))
    gz_path = backup_dir / ('auto_' + ts + '.db.gz')
    with open(tmp_db, 'rb') as f_in, gzip.open(gz_path, 'wb') as f_out:
        shutil.copyfileobj(f_in, f_out)
    tmp_db.unlink()
    to = _get_email_to()
    if not to: return False, 'EMAIL_TO not set'
    from_addr = os.getenv('EMAIL_FROM')
    user = os.getenv('EMAIL_USERNAME')
    pwd = os.getenv('EMAIL_PASSWORD')
    host = os.getenv('EMAIL_SMTP_SERVER', 'smtp.gmail.com')
    port = int(os.getenv('EMAIL_SMTP_PORT', '587'))
    msg = MIMEMultipart()
    msg['From'] = from_addr
    msg['To'] = to
    msg['Subject'] = 'POS Backup - ' + ts
    body = 'نسخة احتياطية تلقائية من نظام POS. الملف مرفق.'
    msg.attach(MIMEText(body, 'plain', 'utf-8'))
    with open(gz_path, 'rb') as f:
        part = MIMEBase('application', 'gzip')
        part.set_payload(f.read())
    encoders.encode_base64(part)
    part.add_header('Content-Disposition', 'attachment; filename=backup_' + ts + '.db.gz')
    msg.attach(part)
    try:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls()
            s.login(user, pwd)
            s.send_message(msg)
        return True, 'sent: ' + gz_path.name
    except Exception as e:
        return False, 'error: ' + str(e)

if __name__ == '__main__':
    from dotenv import load_dotenv; load_dotenv()
    print(send_backup_email())

def local_backup():
    import shutil, os
    import database as db
    from datetime import datetime
    try:
        s = db.query_one("SELECT * FROM backup_settings WHERE company_id=1")
        if not s or s.get("local_enabled") != 1:
            return False, "النسخ المحلي غير مفعل"
        path = s.get("local_path")
        keep = s.get("local_keep", 7)
        if not path or not os.path.exists(path):
            return False, "مسار النسخ غير موجود"
        db_path = os.path.join(os.path.dirname(__file__), "instance", "accounting.db")
        if not os.path.exists(db_path):
            return False, "قاعدة البيانات غير موجودة"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = os.path.join(path, f"local_{timestamp}.db")
        shutil.copy2(db_path, dest)
        files = sorted([f for f in os.listdir(path) if f.startswith("local_") and f.endswith(".db")])
        if len(files) > keep:
            for f in files[:-keep]:
                os.remove(os.path.join(path, f))
        return True, f"تم النسخ إلى {dest}"
    except Exception as e:
        return False, str(e)
