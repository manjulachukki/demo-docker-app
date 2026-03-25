from flask import Flask

app = Flask(__name__)

@app.route("/")
def home():
    return "<h1>Hello from Docker!</h1><p>Your lightweight Flask app is running.</p>"

@app.route("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
