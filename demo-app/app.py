import logging
import sys
from flask import Flask, jsonify, request
from pythonjsonlogger import jsonlogger

app = Flask(__name__)

# --- JSON Structured Logging Setup ---
logger = logging.getLogger()
handler = logging.StreamHandler(sys.stdout)
formatter = jsonlogger.JsonFormatter(
    fmt="%(asctime)s %(name)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S"
)
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)

# Attach JSON handler to Flask's logger as well
app.logger.handlers = logger.handlers
app.logger.setLevel(logging.INFO)


@app.before_request
def log_request():
    app.logger.info(
        "Incoming request",
        extra={"method": request.method, "path": request.path, "remote_addr": request.remote_addr}
    )


@app.route("/")
def home():
    app.logger.info("Home endpoint accessed", extra={"endpoint": "/"})
    return "<h1>Hello from Docker!</h1><p>Your lightweight Flask app is running.</p>"


@app.route("/health")
def health():
    app.logger.info("Health check", extra={"endpoint": "/health", "status": "ok"})
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
