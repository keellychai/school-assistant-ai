"""Optional TF-IDF + Naive Bayes category classifier (trained by train_model.py).

It is only a hint for ranking knowledge entries. If the model file is missing
the app still works; it just ranks without the hint.
"""

import logging

import joblib
from flask import current_app

log = logging.getLogger(__name__)

_model = None
_loaded = False


def _get_model():
    global _model, _loaded
    if not _loaded:
        _loaded = True
        path = current_app.config["CLASSIFIER_PATH"]
        if path.exists():
            try:
                _model = joblib.load(path)
            except Exception:  # corrupted file or incompatible scikit-learn version
                log.exception("Could not load classifier from %s", path)
    return _model


def predict_category(text):
    """Return (category, confidence) or (None, None) when no model is available."""
    model = _get_model()
    if model is None:
        return None, None
    probabilities = model.predict_proba([text])[0]
    best = probabilities.argmax()
    return str(model.classes_[best]), round(float(probabilities[best]), 3)
