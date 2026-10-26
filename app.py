
from pathlib import Path

import joblib
from flask import Flask, jsonify, render_template, request

from services.school_data import search_school_data

BASE_DIR = Path(__file__).resolve().parent
MODEL_PATH = BASE_DIR / "models" / "school_assistant_model.joblib"

app = Flask(__name__)
model = None

if MODEL_PATH.exists():
    model = joblib.load(MODEL_PATH)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")


@app.route("/chatbot")
def chatbot():
    return render_template("chatbot.html")


@app.route("/api/ask", methods=["POST"])
def ask():
    global model

    data = request.get_json(silent=True) or {}
    question = str(data.get("question", "")).strip()

    if not question:
        return jsonify({
            "error": "Please enter a question."
        }), 400

    if len(question) > 500:
        return jsonify({
            "error": "Please keep your question under 500 characters."
        }), 400

    if model is None:
        return jsonify({
            "error": "The AI model is not ready. Run python train_model.py first."
        }), 503

    probabilities = model.predict_proba([question])[0]
    classes = model.classes_
    best_index = probabilities.argmax()

    predicted_category = str(classes[best_index])
    confidence = float(probabilities[best_index])

    item = search_school_data(
        question,
        category=predicted_category
    )

    if item is None:
        return jsonify({
            "answer": (
                "Maaf, saya belum menemukan informasi yang sesuai "
                "dalam data sekolah simulasi. Silakan perjelas "
                "pertanyaan atau hubungi admin sekolah."
            ),
            "source": None,
            "category": predicted_category,
            "confidence": round(confidence, 3),
            "needs_review": True
        })

    return jsonify({
        "answer": item["content"],
        "source": {
            "id": item["id"],
            "title": item["title"]
        },
        "category": item["category"],
        "confidence": round(confidence, 3),
        "needs_review": False
    })


if __name__ == "__main__":
    app.run(debug=True)
