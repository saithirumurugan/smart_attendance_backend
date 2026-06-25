import cv2
import numpy as np
import base64
import logging
import hashlib

try:
    import face_recognition
    FACE_RECOGNITION_AVAILABLE = True
    logging.info("face_recognition library loaded successfully.")
except ImportError:
    FACE_RECOGNITION_AVAILABLE = False
    logging.warning("face_recognition not found. Using OpenCV Haar Cascade fallback.")

# Load OpenCV Haar Cascade for face detection (built into OpenCV, no install needed)
_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')


def _decode_image(base64_image):
    """Decode base64 image to OpenCV BGR image."""
    if ',' in base64_image:
        base64_image = base64_image.split(',')[1]
    img_data = base64.b64decode(base64_image)
    nparr = np.frombuffer(img_data, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    return img


def _detect_face_opencv(img):
    """
    Use OpenCV Haar Cascade to detect face region.
    Returns cropped face region or None.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # Try normal detection first
    faces = _cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(60, 60))

    if len(faces) == 0:
        # Try with looser params for tilted/expression faces
        faces = _cascade.detectMultiScale(gray, scaleFactor=1.05, minNeighbors=2, minSize=(40, 40))

    if len(faces) == 0:
        return None, "No face detected in the image.", 0

    # Pick the largest face
    faces = sorted(faces, key=lambda f: f[2] * f[3], reverse=True)
    x, y, w, h = faces[0]

    # Expand crop slightly for more context
    pad = int(0.15 * w)
    x1 = max(0, x - pad)
    y1 = max(0, y - pad)
    x2 = min(img.shape[1], x + w + pad)
    y2 = min(img.shape[0], y + h + pad)

    face_crop = img[y1:y2, x1:x2]
    return face_crop, None, len(faces)


def _compute_mock_encoding(face_img):
    """
    Compute a stable 128-d encoding from face image using:
    - Multi-channel histograms for colour distribution
    - Spatial grid mean for face layout
    - Edge density for face structure
    All features are concatenated and L2-normalised to a unit vector,
    so cosine distance = 1 - dot_product is always in [0, 2].
    """
    face_resized = cv2.resize(face_img, (64, 64))
    features = []

    # 1. Per-channel histograms (B, G, R) — 32 bins each = 96 values
    for ch in range(3):
        hist = cv2.calcHist([face_resized], [ch], None, [32], [0, 256]).flatten()
        s = hist.sum()
        hist = hist / (s + 1e-6)
        features.extend(hist.tolist())

    # 2. Grayscale 4×4 spatial grid means — 16 values
    gray = cv2.cvtColor(face_resized, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    cell_h, cell_w = gray.shape[0] // 4, gray.shape[1] // 4
    for i in range(4):
        for j in range(4):
            features.append(float(gray[i*cell_h:(i+1)*cell_h, j*cell_w:(j+1)*cell_w].mean()))

    # 3. Edge density in 4×4 grid — 16 values
    edges = cv2.Canny(face_resized, 50, 150).astype(np.float32) / 255.0
    for i in range(4):
        for j in range(4):
            features.append(float(edges[i*cell_h:(i+1)*cell_h, j*cell_w:(j+1)*cell_w].mean()))

    # Clamp to 128 features
    encoding = np.array(features[:128], dtype=np.float64)

    # L2 normalise → unit vector, ensuring cosine dist ∈ [0, 2]
    norm = np.linalg.norm(encoding)
    if norm > 1e-8:
        encoding = encoding / norm
    else:
        encoding = np.zeros(128, dtype=np.float64)

    return encoding.tolist()


def get_face_encoding_from_base64(base64_image):
    """
    Decode a base64 image and return face encoding.
    Used during live attendance scanning.
    """
    try:
        img = _decode_image(base64_image)
        if img is None:
            return None, "Failed to decode image."

        if not FACE_RECOGNITION_AVAILABLE:
            face_crop, err, count = _detect_face_opencv(img)
            if err:
                return None, err
            encoding = _compute_mock_encoding(face_crop)
            return encoding, None

        rgb_img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        face_locations = face_recognition.face_locations(rgb_img, number_of_times_to_upsample=1, model="hog")

        if not face_locations:
            return None, "No face detected in the image."

        face_encodings = face_recognition.face_encodings(rgb_img, face_locations, num_jitters=1)
        if face_encodings:
            return face_encodings[0].tolist(), None

        return None, "Could not extract face features."
    except Exception as e:
        return None, str(e)


def get_face_encoding_for_registration(base64_image):
    """
    Decode a base64 image and return face encoding for registration.
    Uses higher quality settings for a stable reference encoding.
    """
    try:
        img = _decode_image(base64_image)
        if img is None:
            return None, "Failed to decode image."

        if not FACE_RECOGNITION_AVAILABLE:
            face_crop, err, count = _detect_face_opencv(img)
            if err:
                return None, err
            # Compute encoding — same stable method as live scanning
            encoding = _compute_mock_encoding(face_crop)
            return encoding, None

        rgb_img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        face_locations = face_recognition.face_locations(rgb_img, number_of_times_to_upsample=2, model="hog")

        if not face_locations:
            return None, "No face detected in the image."

        face_encodings = face_recognition.face_encodings(rgb_img, face_locations, num_jitters=5)
        if face_encodings:
            return face_encodings[0].tolist(), None

        return None, "Could not extract face features."
    except Exception as e:
        return None, str(e)


def match_face(unknown_encoding, known_encodings, tolerance=0.30):
    """
    Compare unknown face encoding against known encodings.
    Returns (best_match_index, best_distance) or (None, distance).

    Mock mode  : cosine distance on L2-normalised histogram vectors.
                 Tolerance 0.30 → same person across expressions/lighting.
    Real mode  : euclidean face_distance. Tolerance 0.58.
    """
    try:
        if not known_encodings:
            return None, None

        unknown_np = np.array(unknown_encoding, dtype=np.float64)
        # Re-normalise in case of any drift
        un_norm = np.linalg.norm(unknown_np)
        if un_norm > 1e-8:
            unknown_np = unknown_np / un_norm

        if not FACE_RECOGNITION_AVAILABLE:
            best_index = None
            best_dist  = float('inf')

            for i, enc in enumerate(known_encodings):
                kn = np.array(enc, dtype=np.float64)
                kn_norm = np.linalg.norm(kn)
                if kn_norm > 1e-8:
                    kn = kn / kn_norm

                # Cosine distance: 0 = identical, 2 = opposite
                cos_sim  = float(np.dot(unknown_np, kn))
                cos_dist = 1.0 - cos_sim   # always in [0, 2]

                print(f"[MockMatch] Person {i}: cosine_dist={cos_dist:.4f}")
                if cos_dist < best_dist:
                    best_dist  = cos_dist
                    best_index = i

            print(f"[MockMatch] Best dist={best_dist:.4f}, tolerance={tolerance}")
            if best_dist <= tolerance:
                return best_index, best_dist
            return None, best_dist

        # ── Real face_recognition ────────────────────────────
        known_np   = [np.array(enc) for enc in known_encodings]
        distances  = face_recognition.face_distance(known_np, unknown_np)
        best_idx   = int(np.argmin(distances))
        best_dist  = float(distances[best_idx])
        print(f"[FaceMatch] Best distance: {best_dist:.4f} (tolerance: {tolerance})")

        if best_dist <= tolerance:
            return best_idx, best_dist
        return None, best_dist

    except Exception as e:
        print(f"Error matching face: {e}")
        return None, None
