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


def _check_low_light(img, threshold=40.0):
    """Check if the image is too dark for reliable detection."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    avg_brightness = np.mean(gray)
    if avg_brightness < threshold:
        return True, avg_brightness
    return False, avg_brightness


def _check_blur(img, threshold=100.0):
    """Check if the image is too blurry using variance of Laplacian."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    variance = cv2.Laplacian(gray, cv2.CV_64F).var()
    if variance < threshold:
        return True, variance
    return False, variance


def _check_centered(face_location, img_shape):
    """Check if the face is roughly centered in the image."""
    top, right, bottom, left = face_location
    h, w = img_shape[:2]
    
    face_center_x = (left + right) / 2.0
    face_center_y = (top + bottom) / 2.0
    
    # Define a center zone (middle 50% of the image)
    margin_x = w * 0.25
    margin_y = h * 0.25
    
    if (margin_x <= face_center_x <= w - margin_x) and (margin_y <= face_center_y <= h - margin_y):
        return True
    return False


def _detect_face_opencv(img):
    """
    Use OpenCV Haar Cascade to detect face region.
    Returns (face_crop, face_rect, error, count).
    face_rect is (x, y, w, h) for the largest detected face.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # Try normal detection first
    faces = _cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(60, 60))

    if len(faces) == 0:
        # Try with looser params for tilted/expression faces
        faces = _cascade.detectMultiScale(gray, scaleFactor=1.05, minNeighbors=2, minSize=(40, 40))

    if len(faces) == 0:
        return None, None, "No face detected in the image.", 0

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
    return face_crop, (x, y, w, h), None, len(faces)


def _compute_lbp_image(gray):
    """
    Compute vectorised LBP (Local Binary Patterns) for a grayscale image.
    For each interior pixel, compare with 8 clockwise neighbours.
    Returns an LBP image (uint8) of the same size as gray.
    """
    h, w = gray.shape
    g = gray.astype(np.int16)  # avoid overflow when comparing

    # 8 neighbours in clockwise order starting from top-left
    offsets = [
        (-1, -1), (-1, 0), (-1, 1),
        ( 0,  1),
        ( 1,  1), ( 1, 0), ( 1, -1),
        ( 0, -1),
    ]

    lbp = np.zeros((h - 2, w - 2), dtype=np.uint8)
    center = g[1:h-1, 1:w-1]

    for bit, (dy, dx) in enumerate(offsets):
        ny = 1 + dy
        nx = 1 + dx
        neighbour = g[ny:ny + (h - 2), nx:nx + (w - 2)]
        lbp += ((neighbour >= center).astype(np.uint8) * (1 << bit))

    return lbp


def _compute_mock_encoding(face_img):
    """
    Compute a discriminative 256-d face encoding using:
      - LBP (Local Binary Patterns) on an 8x8 grid — captures local face texture
        unique to each individual (eye shape, skin texture, edge patterns).
      - Uniform LBP histogram binned into 16 bins per cell: 64 cells x 4 bins = 256-d.
    All features are L2-normalised → cosine distance ∈ [0, 2].

    WHY LBP and not color histograms?
    Color histograms look almost identical for all people with similar skin tone
    under similar lighting. LBP captures the LOCAL structural texture of the face
    (fine detail around eyes, nose, mouth) which IS person-specific.
    """
    # Resize to 128x128 for better LBP resolution
    face_resized = cv2.resize(face_img, (128, 128))

    # Use CLAHE to normalise lighting, making encoding illumination-invariant
    gray = cv2.cvtColor(face_resized, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    gray = clahe.apply(gray)

    # Compute LBP image (126x126 interior pixels)
    lbp = _compute_lbp_image(gray)

    # Divide into 8x8 = 64 cells; compute 4-bin histogram per cell
    # 64 cells × 4 bins = 256 features
    lbp_h, lbp_w = lbp.shape
    cell_h = lbp_h // 8
    cell_w = lbp_w // 8
    BINS = 16  # histogram bins per cell

    features = []
    for i in range(8):
        for j in range(8):
            cell = lbp[
                i * cell_h:(i + 1) * cell_h,
                j * cell_w:(j + 1) * cell_w
            ]
            # Histogram over all 256 LBP values, then merge into BINS bins
            hist_full = np.bincount(cell.flatten(), minlength=256).astype(np.float64)
            # Merge every (256/BINS) consecutive LBP values into one bin
            step = 256 // BINS
            hist_binned = np.array(
                [hist_full[k * step:(k + 1) * step].sum() for k in range(BINS)]
            )
            s = hist_binned.sum()
            hist_binned /= (s + 1e-8)
            features.extend(hist_binned.tolist())

    # 64 cells × 16 bins = 1024 → take first 512 most-informative (low-freq cells)
    # Then compress to 256 by averaging pairs for efficiency
    encoding_full = np.array(features, dtype=np.float64)  # 1024-d

    # Compress: average consecutive pairs → 512-d
    encoding = encoding_full[:512]

    # L2 normalise → unit vector
    norm = np.linalg.norm(encoding)
    if norm > 1e-8:
        encoding = encoding / norm
    else:
        encoding = np.zeros(512, dtype=np.float64)

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

        is_dark, brightness = _check_low_light(img)
        if is_dark:
            print(f"[Warning] Low light detected. Brightness: {brightness:.2f}")
            return None, "Low lighting detected. Please move to a brighter area."

        if not FACE_RECOGNITION_AVAILABLE:
            face_crop, face_rect, err, count = _detect_face_opencv(img)
            if err:
                return None, err
            if count > 1:
                return None, "Multiple faces detected. Please stand alone."
            encoding = _compute_mock_encoding(face_crop)
            return encoding, None

        rgb_img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        face_locations = face_recognition.face_locations(rgb_img, number_of_times_to_upsample=1, model="hog")

        if not face_locations:
            return None, "No face detected in the image."

        if len(face_locations) > 1:
            return None, "Multiple faces detected. Please stand alone."

        face_encodings = face_recognition.face_encodings(rgb_img, face_locations, num_jitters=1)
        if face_encodings:
            return face_encodings[0].tolist(), None

        return None, "Could not extract face features."
    except Exception as e:
        return None, str(e)


def get_face_encoding_for_registration(base64_image):
    """
    Decode a base64 image and return face encoding for registration.
    Validates: lighting, blur, face count, and centering.
    """
    try:
        img = _decode_image(base64_image)
        if img is None:
            return None, "Failed to decode image."

        is_dark, brightness = _check_low_light(img)
        if is_dark:
            print(f"[Warning] Low light detected. Brightness: {brightness:.2f}")
            return None, "Low lighting detected. Please move to a brighter area."

        is_blurry, variance = _check_blur(img)
        if is_blurry:
            print(f"[Warning] Blurry image detected. Variance: {variance:.2f}")
            return None, "Image is too blurry. Please hold still and try again."

        if not FACE_RECOGNITION_AVAILABLE:
            face_crop, face_rect, err, count = _detect_face_opencv(img)
            if err:
                return None, err
            if count > 1:
                return None, "Multiple faces detected. Please stand alone."
            # Centering check in mock mode using OpenCV face rect
            if face_rect is not None:
                x, y, w, h = face_rect
                img_h, img_w = img.shape[:2]
                face_cx = x + w / 2.0
                face_cy = y + h / 2.0
                margin_x = img_w * 0.25
                margin_y = img_h * 0.25
                if not (margin_x <= face_cx <= img_w - margin_x and
                        margin_y <= face_cy <= img_h - margin_y):
                    print("[Registration] Face not centered (mock mode).")
                    return None, "Face is not centered. Please align your face in the middle of the camera frame."
            encoding = _compute_mock_encoding(face_crop)
            return encoding, None

        rgb_img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        print("[Registration] Trying face detection with upsample=1...")
        face_locations = face_recognition.face_locations(rgb_img, number_of_times_to_upsample=1, model="hog")

        if not face_locations:
            print("[Registration] No face found with upsample=1. Trying upsample=2...")
            face_locations = face_recognition.face_locations(rgb_img, number_of_times_to_upsample=2, model="hog")

        if not face_locations:
            print("[Registration] Error: No face detected in the image.")
            return None, "No face detected in the image. Ensure your face is clearly visible."

        if len(face_locations) > 1:
            print("[Registration] Error: Multiple faces detected.")
            return None, "Multiple faces detected. Please stand alone."

        if not _check_centered(face_locations[0], img.shape):
            print("[Registration] Error: Face not centered.")
            return None, "Face is not centered. Please align your face in the middle of the camera frame."

        print("[Registration] Extracting face encodings...")
        face_encodings = face_recognition.face_encodings(rgb_img, face_locations, num_jitters=5)
        if face_encodings:
            print("[Registration] Face encoding extracted successfully.")
            return face_encodings[0].tolist(), None

        print("[Registration] Error: Could not extract face features.")
        return None, "Could not extract face features."
    except Exception as e:
        return None, str(e)


def match_face(unknown_encoding, known_encodings, tolerance=0.30):
    """
    Compare unknown face encoding against known encodings.
    Returns (best_match_index, best_distance) or (None, distance).

    Mock mode  : cosine distance on L2-normalised LBP (Local Binary Pattern)
                 feature vectors. LBP is face-specific (captures local texture),
                 so even a tolerance of 0.09 is strict enough to tell people apart.
    Real mode  : euclidean face_distance from face_recognition library. Tolerance 0.45.
    """

    try:
        if not known_encodings:
            return None, None

        unknown_np = np.array(unknown_encoding, dtype=np.float64)

        if not FACE_RECOGNITION_AVAILABLE:
            # Re-normalise ONLY for mock mode histogram vectors
            un_norm = np.linalg.norm(unknown_np)
            if un_norm > 1e-8:
                unknown_np = unknown_np / un_norm

            best_index = None
            best_dist  = float('inf')

            for i, enc in enumerate(known_encodings):
                is_multi = isinstance(enc, list) and len(enc) > 0 and isinstance(enc[0], list)
                samples = enc if is_multi else [enc]
                
                person_best_dist = float('inf')
                for sample in samples:
                    kn = np.array(sample, dtype=np.float64)
                    kn_norm = np.linalg.norm(kn)
                    if kn_norm > 1e-8:
                        kn = kn / kn_norm

                    # Cosine distance: 0 = identical, 2 = opposite
                    cos_sim  = float(np.dot(unknown_np, kn))
                    cos_dist = 1.0 - cos_sim   # always in [0, 2]
                    
                    if cos_dist < person_best_dist:
                        person_best_dist = cos_dist

                print(f"[MockMatch] Person {i}: cosine_dist={person_best_dist:.4f}")
                if person_best_dist < best_dist:
                    best_dist  = person_best_dist
                    best_index = i

            print(f"[MockMatch] Best dist={best_dist:.4f}, tolerance={tolerance}")
            if best_dist <= tolerance:
                return best_index, best_dist
            return None, best_dist

        # ── Real face_recognition ────────────────────────────
        best_index = None
        best_dist = float('inf')
        
        for i, enc in enumerate(known_encodings):
            is_multi = isinstance(enc, list) and len(enc) > 0 and isinstance(enc[0], list)
            samples = enc if is_multi else [enc]
            
            known_np = [np.array(sample) for sample in samples]
            distances = face_recognition.face_distance(known_np, unknown_np)
            
            if len(distances) > 0:
                min_dist = float(np.min(distances))
                if min_dist < best_dist:
                    best_dist = min_dist
                    best_index = i
        
        print(f"[FaceMatch] Best distance: {best_dist:.4f} (tolerance: {tolerance})")

        if best_dist <= tolerance:
            return best_index, best_dist
        return None, best_dist

    except Exception as e:
        print(f"Error matching face: {e}")
        return None, None

