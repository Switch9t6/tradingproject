"""
Flask Web Application & REST API Server
=======================================
Serves interactive report web dashboard (GET /report), AJAX metrics API (GET /api/report_data),
and executive PDF report download (GET /download-report-pdf).
"""

import os
import sys
import threading
from typing import Optional
import io
from flask import Flask, render_template, request, jsonify, Response, send_file

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reports.trade_report import get_trade_report_data, validate_date_range, get_ist_today_str
from reports.pdf_generator import generate_trade_report_pdf

# Initialize Flask app pointing to web/templates directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")

app = Flask(__name__, template_folder=TEMPLATE_DIR)

@app.route("/")
@app.route("/report")
def report_page():
    """Renders interactive HTML performance report web dashboard."""
    token = request.args.get("token", "")
    return render_template("report.html", token=token)

@app.route("/webhook", methods=["GET", "POST"])
def fyers_webhook():
    """Fyers Webhook validation & order update listener."""
    if request.method == "GET":
        return jsonify({"status": "ACTIVE", "message": "Fyers Webhook Endpoint Active"}), 200

    try:
        data = request.get_json(silent=True) or request.form.to_dict()
        print(f"[FYERS WEBHOOK RECEIVED] {data}")
        return jsonify({"status": "SUCCESS", "message": "Webhook payload received"}), 200
    except Exception as ex:
        return jsonify({"status": "ERROR", "error": str(ex)}), 400

@app.route("/api/report_data")
def api_report_data():
    """
    AJAX Endpoint: Returns performance report data as JSON.
    Query parameters: start_date (YYYY-MM-DD), end_date (YYYY-MM-DD).
    """
    today_str = get_ist_today_str()
    start_date = request.args.get("start_date", today_str)
    end_date = request.args.get("end_date", start_date)

    is_valid, err_msg = validate_date_range(start_date, end_date)
    if not is_valid:
        return jsonify({"error": err_msg}), 400

    try:
        data = get_trade_report_data(start_date=start_date, end_date=end_date)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/download-report-pdf")
def download_report_pdf():
    """
    API Endpoint: Generates and streams PDF report as attachment.
    Query parameters: start_date (YYYY-MM-DD), end_date (YYYY-MM-DD).
    Rejects requests exceeding 365 days range with HTTP 400.
    """
    today_str = get_ist_today_str()
    start_date = request.args.get("start_date", today_str)
    end_date = request.args.get("end_date", start_date)

    is_valid, err_msg = validate_date_range(start_date, end_date)
    if not is_valid:
        return jsonify({"error": err_msg}), 400

    try:
        pdf_bytes, filename = generate_trade_report_pdf(start_date=start_date, end_date=end_date)
        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

_server_started = False

def start_web_server_background(host: str = "0.0.0.0", port: Optional[int] = None):
    """
    Launches Flask web server in a background daemon thread.
    """
    global _server_started
    if _server_started:
        return

    if port is None:
        port = int(os.getenv("PORT", "5000"))

    def _run():
        print(f"[Web Server] Starting interactive report web server on http://{host}:{port}...")
        # Disable Flask reloader & extra debug threads for background process safety
        app.run(host=host, port=port, debug=False, use_reloader=False)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    _server_started = True

if __name__ == "__main__":
    port_num = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port_num, debug=True)
