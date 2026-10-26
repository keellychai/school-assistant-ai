
from pathlib import Path

import joblib
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.naive_bayes import MultinomialNB
from sklearn.metrics import accuracy_score, classification_report

BASE_DIR = Path(__file__).resolve().parent
DATA_PATH = BASE_DIR / "data" / "inquiries.csv"
MODEL_DIR = BASE_DIR / "models"
MODEL_PATH = MODEL_DIR / "school_assistant_model.joblib"

data = pd.read_csv(DATA_PATH).dropna()
data["text"] = data["text"].astype(str).str.strip()
data["label"] = data["label"].astype(str).str.strip()
data = data[(data["text"] != "") & (data["label"] != "")]

if data["label"].value_counts().min() < 2:
    raise ValueError("Each category needs at least two training examples.")

X_train, X_test, y_train, y_test = train_test_split(
    data["text"],
    data["label"],
    test_size=0.25,
    random_state=42,
    stratify=data["label"]
)

model = Pipeline([
    ("tfidf", TfidfVectorizer(
        lowercase=True,
        ngram_range=(1, 2),
        strip_accents="unicode"
    )),
    ("classifier", MultinomialNB())
])

model.fit(X_train, y_train)
predictions = model.predict(X_test)

print("Test accuracy:", round(accuracy_score(y_test, predictions), 3))
print(classification_report(y_test, predictions, zero_division=0))

MODEL_DIR.mkdir(parents=True, exist_ok=True)
joblib.dump(model, MODEL_PATH)

print(f"Model saved to: {MODEL_PATH}")
