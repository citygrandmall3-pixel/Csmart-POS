import schedule
import time
import os
import smtplib
from email.message import EmailMessage
import database as db
from dotenv import load_dotenv

load_dotenv()

def job():
    try:
        print("[Scheduler] Checking backup settings from DB...")
        s = db.query_one("SELECT * FROM backup_settings WHERE company_id=1")
        
        if not s:
            print("[Scheduler] No settings found. Skipping.")
            return
            
        if s.get("email_enabled") != 1 or s.get("email_daily") != 1:
            print("[Scheduler] Auto backup is disabled. Skipping.")
            return
            
        from email_backup import local_backup
        lok, lmsg = local_backup()
        print(f"[Scheduler] Local backup: {lmsg}")
        print("[Scheduler] Auto backup enabled. Sending email via SSL (Port 465)...")
        
        to_email = (s.get("email_to") or "").strip()
        if not to_email:
            print("[Scheduler] No email_to found. Skipping.")
            return

        # استخدام إعدادات SSL (منفذ 465) لتجاوز حظر الشبكة
        msg = EmailMessage()
        msg.set_content("هذه نسخة احتياطية تلقائية من نظام المحاسبة.")
        msg['Subject'] = "نسخة احتياطية تلقائية (Scheduler SSL)"
        msg['From'] = os.getenv("EMAIL_FROM", to_email)
        msg['To'] = to_email
        
        # المنفذ 465 يستخدم SMTP_SSL
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as server:
            server.login(os.getenv("EMAIL_FROM", ""), os.getenv("EMAIL_PASSWORD", ""))
            server.send_message(msg)
            
        print("[Scheduler] Email sent successfully via SSL!")
        
    except Exception as e:
        print(f"[Scheduler] Error: {e}")

schedule.every(1).hours.do(job)

if __name__ == "__main__":
    print("Scheduler started...")
    job()
    while True:
        schedule.run_pending()
        time.sleep(60)
def start_scheduler(hours=1):
    import threading
    def run_scheduler():
        while True:
            schedule.run_pending()
            time.sleep(60)
            
    thread = threading.Thread(target=run_scheduler)
    thread.daemon = True
    thread.start()
    print("Scheduler started in background thread.")
